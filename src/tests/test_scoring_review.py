"""Unranked source review and explicit label-export boundaries; fictional data."""
import copy
import hashlib
import json

import pytest

from src.retrieval import scoring_review as review
from src.retrieval.scoring import FEATURES
from scripts import prepare_scoring_review as cli

DOC = '11111111-1111-4111-8111-111111111111'
REV = '22222222-2222-4222-8222-222222222222'
GOOD = '33333333-3333-4333-8333-333333333333'
BAD = '44444444-4444-4444-8444-444444444444'
RUN = '55555555-5555-4555-8555-555555555555'


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode('utf-8')


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(review, 'REPOSITORY_ROOT', tmp_path)
    private = tmp_path / '.tmp'
    private.mkdir()
    package = tmp_path / 'chunks' / DOC
    package.mkdir(parents=True)
    text = 'Fictional <script>alert("x")</script> policy.'
    chunks = [{'content': text + str(i), 'source_refs': [dict(segment_id='s', start=0, end=len(text))]}
              for i in range(3)]
    files = {'metadata.json': encoded({'document_id': DOC, 'revision_id': REV}),
        'chunks.json': encoded({'chunks': chunks}),
        'extraction.json': encoded({'segments': [{'segment_id': 's', 'text': text,
                                                  'location': {'page': 1}}]}),
        'source.md': text.encode()}
    for name, raw in files.items():
        (package / name).write_bytes(raw)
    snapshot = lambda path: {name: (path / name).read_bytes() for name in files}
    def load(root, path, **kwargs):
        current = snapshot(path)
        # Match the real loader: normalized publication chunks deliberately
        # have no source_refs. Those exist only in files['chunks.json'].
        normalized = [dict(chunk_index=i, content=c['content'],
            source_location={'spans': [{'segment_id': 's', 'start': 0, 'end': len(text)}]},
            extraction_method='native') for i, c in enumerate(json.loads(current['chunks.json'])['chunks'])]
        return json.loads(current['metadata.json']), {'chunks': normalized}, current
    monkeypatch.setattr(review.packages, 'load_package', load)
    monkeypatch.setattr(review.packages, 'snapshot_files', snapshot)
    monkeypatch.setattr(review.packages, 'package_paths', lambda root: [package])
    ref = lambda i: dict(document_id=DOC, revision_id=REV, chunk_index=i)
    cases = [dict(id=f'case-{i}', question=f'Fictional <img src=x onerror="x"> question {i}',
        domain='fictional', intent_group=f'family-{i}', split='regression', relevant=i == 0,
        critical=False, top1_required=False, wording='direct', unit_ids=['unit'] if i == 0 else [],
        required_groups=[[ref(0)]] if i == 0 else [], candidate_labels={}) for i in range(2)]
    dataset = dict(schema_version=1, reviewed=False, review_notes='', required_units=['unit'],
        cases=cases, packages=[dict(path=f'chunks/{DOC}', sha256=review.packages.files_hash(files))])
    matches = [dict(ref(i), chunk_id=identity, run_id=RUN, rerank_score=9. - i,
                    cosine_distance=.1) for i, identity in enumerate((GOOD, BAD))]
    rows = [dict(id=c['id'], after_rerank=list(reversed(matches)),
        scoring_features={identity: [float(i)] * len(FEATURES)
                          for i, identity in enumerate((GOOD, BAD))}) for c in cases]
    collection = dict(collect_only=True, database_and_packages_unchanged=True,
        feature_names=list(FEATURES), corpus_fingerprint='fictional-corpus',
        pipelines={arm: dict(candidate_collection=copy.deepcopy(rows)) for arm in ('baseline', 'semantic')})
    dataset_path, collection_path = private / 'dataset.json', private / 'collection.json'
    def save():
        dataset_path.write_bytes(encoded(dataset))
        collection['dataset_sha256'] = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        collection_path.write_bytes(encoded(collection))
    save()
    return dataset_path, collection_path, dataset, collection, save, package


def accepted(inputs):
    dataset, collection = inputs[:2]
    _, declaration, _ = review.worksheet(dataset, collection)
    for key in ('human_approved', 'source_evidence_checked', 'candidate_labels_checked',
                'split_independence_checked', 'case_definitions_checked'):
        declaration[key] = True
    declaration['review_statement'] = 'Actual fictional source-label review for this test.'
    declaration['candidate_labels'] = {'case-0': {GOOD: True, BAD: False},
                                        'case-1': {GOOD: False, BAD: False}}
    path = dataset.parent / 'accepted-review.json'
    path.write_bytes(encoded(declaration))
    return path, declaration


def test_worksheet_is_unranked_unapproved_source_material_and_html_is_escaped(inputs):
    data, declaration, page = review.worksheet(*inputs[:2])
    assert [c['chunk_id'] for c in data['cases'][0]['candidates']] == [GOOD, BAD]
    assert data['cases'][0]['candidates'][0]['source_excerpts'][0]['location'] == {'page': 1}
    assert declaration['human_approved'] is False
    assert all(label is None for values in declaration['candidate_labels'].values() for label in values.values())
    assert '<script>' not in page and '<img ' not in page
    assert '&lt;script&gt;' in page and '&lt;img ' in page
    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(v) for v in value.values()), set())
        if isinstance(value, list):
            return set().union(*(keys(v) for v in value), set())
        return set()
    assert not keys(data) & {'rerank_score', 'cosine_distance', 'rank', 'scoring_features', 'feature_names'}


def test_export_preserves_features_without_questions_and_uses_union_labels(inputs):
    path, _ = accepted(inputs)
    exported = review.export_development(*inputs[:2], path)
    assert exported['split'] == 'development' and exported['reviewed'] is True
    assert len(exported['rows']) == 8
    assert all(row['required_groups'] == [[GOOD]] for row in exported['rows'] if row['case_id'] == 'case-0')
    assert {row['arm'] for row in exported['rows']} == {'baseline', 'semantic'}
    assert all(row['relevant'] is (row['case_id'] == 'case-0' and row['chunk_id'] == GOOD)
               for row in exported['rows'])
    assert 'question' not in json.dumps(exported) and 'Fictional' not in json.dumps(exported)


@pytest.mark.parametrize('change', ['count', 'content', 'index'])
def test_authored_refs_must_match_normalized_publication_chunks(inputs, monkeypatch, change):
    original = review.packages.load_package
    def mismatched(*args, **kwargs):
        record, payload, files = original(*args, **kwargs)
        if change == 'count': payload['chunks'].pop()
        if change == 'content': payload['chunks'][0]['content'] = 'Different content'
        if change == 'index': payload['chunks'][0]['chunk_index'] = 9
        return record, payload, files
    monkeypatch.setattr(review.packages, 'load_package', mismatched)
    with pytest.raises(ValueError, match='scoring_authored_publication_mismatch'):
        review.worksheet(*inputs[:2])


@pytest.mark.parametrize('change', ['unapproved', 'unchecked', 'statement', 'hash', 'extra_case', 'missing_uuid', 'unknown'])
def test_export_requires_complete_actual_bound_source_review(inputs, change):
    path, declaration = accepted(inputs)
    if change == 'unapproved': declaration['human_approved'] = False
    if change == 'unchecked': declaration['case_definitions_checked'] = False
    if change == 'statement': declaration['review_statement'] = ' '
    if change == 'hash': declaration['collection_sha256'] = '0' * 64
    if change == 'extra_case': declaration['candidate_labels']['extra'] = {}
    if change == 'missing_uuid': declaration['candidate_labels']['case-0'].pop(BAD)
    if change == 'unknown': declaration['candidate_labels']['case-0'][BAD] = None
    path.write_bytes(encoded(declaration))
    with pytest.raises(ValueError, match='required|mismatch'):
        review.export_development(*inputs[:2], path)


@pytest.mark.parametrize('split', ['calibration', 'holdout'])
def test_export_cannot_turn_acceptance_cases_into_development(inputs, split):
    inputs[2]['cases'][0]['split'] = split
    inputs[4]()
    path, _ = accepted(inputs)
    with pytest.raises(ValueError, match='cannot_consume_acceptance'):
        review.export_development(*inputs[:2], path)


def test_required_pool_miss_is_explicit_and_identical_across_arms(inputs):
    inputs[2]['cases'][0]['required_groups'][0][0]['chunk_index'] = 2
    inputs[3]['pipelines']['baseline']['candidate_collection'][0]['after_rerank'].pop()
    inputs[3]['pipelines']['baseline']['candidate_collection'][0]['scoring_features'].pop(GOOD)
    inputs[4]()
    path, declaration = accepted(inputs)
    # GOOD remains in the semantic union and is a related additional passage;
    # the actual required source at index2 never appeared in any candidate set.
    exported = review.export_development(*inputs[:2], path)
    groups = [row['required_groups'] for row in exported['rows'] if row['case_id'] == 'case-0']
    assert groups and all(group == [[f'source:{DOC}:{REV}:2']] for group in groups)


def test_stale_source_and_collection_hashes_reject_before_worksheet(inputs):
    inputs[2]['packages'][0]['sha256'] = '0' * 64
    inputs[4]()
    with pytest.raises(ValueError, match='source_pin_changed'):
        review.worksheet(*inputs[:2])


def test_collection_without_successful_readonly_integrity_cannot_be_reviewed(inputs):
    inputs[3]['database_and_packages_unchanged'] = False
    inputs[4]()
    with pytest.raises(ValueError, match='scoring_collection_binding_mismatch'):
        review.worksheet(*inputs[:2])


def test_same_uuid_cannot_claim_different_source_identity_across_arms(inputs):
    row = inputs[3]['pipelines']['semantic']['candidate_collection'][0]
    row['after_rerank'][0]['chunk_index'] = 2
    inputs[4]()
    with pytest.raises(ValueError, match='conflicting_scoring_candidate_identity'):
        review.worksheet(*inputs[:2])


def test_empty_case_arm_can_be_reviewed_but_not_silently_omitted_from_training(inputs):
    row = inputs[3]['pipelines']['baseline']['candidate_collection'][0]
    row.update(after_rerank=[], scoring_features={})
    inputs[4]()
    path, _ = accepted(inputs)
    with pytest.raises(ValueError, match='empty_scoring_case_arm_requires_diagnostic'):
        review.export_development(*inputs[:2], path)


def test_cli_private_outputs_are_new_and_never_profiles(inputs, capsys):
    output = inputs[0].parent / 'human-review'
    args = ['worksheet', '--dataset', str(inputs[0]), '--collection', str(inputs[1]),
            '--output-dir', str(output)]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)['accepted_profile'] is False
    assert {p.name for p in output.iterdir()} == {'source-review.json', 'human-review.json', 'index.html'}
    assert cli.main(args) == 2
    assert cli.main([*args[:-1], str(inputs[0].parent.parent / 'outside')]) == 2
