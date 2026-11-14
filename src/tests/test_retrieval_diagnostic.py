"""Offline diagnostic provenance, unresolved labels and output isolation."""
import hashlib
import json

import pytest

from scripts import diagnose_retrieval as diagnostic


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, 'REPOSITORY_ROOT', tmp_path)
    package = tmp_path / 'chunks' / 'fictional'
    chunks = [{'content': 'Fictional leave policy.',
               'source_refs': [{'segment_id': 'one', 'start': 0, 'end': 21}]},
              {'content': 'Fictional building access.', 'source_refs': []}]
    write(package / 'metadata.json', {'revision_id': 'revision'})
    write(package / 'chunks.json', {'chunks': chunks})
    write(package / 'extraction.json', {'segments': [
        {'segment_id': 'one', 'text': 'Fictional leave policy.', 'location': {'page_start': 1}}]})
    def load_package(root, directory, **kwargs):
        names = ('metadata.json', 'chunks.json', 'extraction.json')
        files = {name: (directory / name).read_bytes() for name in names}
        return json.loads(files['metadata.json']), json.loads(files['chunks.json']), files
    monkeypatch.setattr(diagnostic.packages, 'load_package', load_package)
    chunk_hash = hashlib.sha256(json.dumps(chunks, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    case_path = tmp_path / '.tmp' / 'cases.json'
    write(case_path, {'revision_id': 'revision', 'chunks_sha256': chunk_hash, 'cases': [
        {'id': 'case', 'question': 'How to request leave?', 'split': 'regression',
         'intent_group': 'leave', 'relevant': True, 'required_groups': [[0]]}]})
    manifest = tmp_path / '.tmp' / 'manifest.json'
    write(manifest, {'datasets': [{'package': 'chunks/fictional', 'cases': '.tmp/cases.json'}]})
    collection = tmp_path / '.tmp' / 'collection.json'
    matches = [dict(chunk_id='other', revision_id='revision', chunk_index=1, rerank_score=8.),
               dict(chunk_id='evidence', revision_id='revision', chunk_index=0, rerank_score=2.)]
    write(collection, {'manifest_sha256': diagnostic.digest(manifest),
        'corpus_fingerprint': 'corpus', 'fingerprint': 'model', 'cases': [
            {'id': 'case', 'before_rerank': list(reversed(matches)), 'after_rerank': matches}]})
    reference = tmp_path / '.tmp' / 'old.json'
    write(reference, {'corpus_fingerprint': 'corpus', 'fingerprint': 'model',
                      'calibration': {'threshold': 7.}})
    return manifest, collection, reference, case_path


def test_diagnostic_distinguishes_retrieval_from_historical_filter_and_keeps_review_unranked(inputs):
    manifest, collection, reference, _ = inputs
    before = {p: p.read_bytes() for p in manifest.parent.parent.rglob('*.json')}
    report, review, _ = diagnostic.analyze(manifest, collection, reference=reference)
    hit = report['cases'][0]['evidence_groups'][0]['hits'][0]
    assert (hit['before_rank'], hit['after_rank'], hit['passes_historical_cutoff']) == (1, 2, False)
    assert report['profile_generated'] is False
    assert review['cases'][0]['target_package'] == 'chunks/fictional'
    assert review['cases'][0]['target_revision_id'] == 'revision'
    entries = review['cases'][0]['candidates']
    assert entries[0]['chunk_index'] == 0
    assert entries[0]['source_excerpts'][0]['text'] == 'Fictional leave polic'
    assert all(c['reviewed_label'] is None and c['previous_label'] is None for c in entries)
    assert not review['reviewed'] and not review['labels_complete']
    forbidden = {'score', 'rerank_score', 'cosine_distance', 'before_rank', 'after_rank', 'threshold'}
    def check(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
    check(review)
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize('mutation,code', [
    ('stale_case', 'stale_cases'), ('missing_candidate', 'candidate_pool_mismatch'),
    ('partial_labels', 'incomplete_candidate_labels'), ('stale_reference', 'reference_identity_mismatch')])
def test_diagnostic_rejects_unreliable_provenance(inputs, mutation, code):
    manifest, collection, reference, case_path = inputs
    if mutation in {'stale_case', 'partial_labels'}:
        data = json.loads(case_path.read_text())
        if mutation == 'stale_case':
            data['chunks_sha256'] = 'stale'
        else:
            data['cases'][0]['candidate_labels'] = {'evidence': True}
        write(case_path, data)
    elif mutation == 'missing_candidate':
        data = json.loads(collection.read_text())
        data['cases'][0]['before_rerank'].pop()
        write(collection, data)
    else:
        data = json.loads(reference.read_text())
        data['corpus_fingerprint'] = 'different'
        write(reference, data)
    with pytest.raises(ValueError, match=code):
        diagnostic.analyze(manifest, collection, reference=reference)


def test_command_never_overwrites_inputs_or_outputs_and_does_not_print_private_content(inputs, capsys):
    manifest, collection, reference, _ = inputs
    new = manifest.parent / 'diagnostic'
    argv = ['--manifest', str(manifest), '--collection', str(collection),
            '--reference', str(reference), '--output', str(new)]
    assert diagnostic.main(argv) == 0
    assert set(p.name for p in new.iterdir()) == {'diagnostic.json', 'source-review.json'}
    first = (new / 'diagnostic.json').read_bytes()
    assert diagnostic.main(argv) == 2
    assert (new / 'diagnostic.json').read_bytes() == first
    argv[-1] = str(manifest.parent.parent / 'chunks' / 'new')
    assert diagnostic.main(argv) == 2
    assert not (manifest.parent.parent / 'chunks' / 'new').exists()
    output = capsys.readouterr().out
    assert 'Fictional' not in output and 'How to request leave' not in output
