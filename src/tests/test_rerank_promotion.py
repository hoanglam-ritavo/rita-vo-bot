"""Synthetic promotion protocol: acceptance gates and current byte copies."""
import hashlib
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from src.retrieval.rerank_promotion import PromotionError, promote_profile
from src.retrieval.scoring import FEATURES, scoring_digest
from src.retrieval.semantic_evaluation import compare, summarize


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


def fixture(root):
    private = root / '.tmp' / 'evaluation'
    private.mkdir(parents=True)
    document, revision = '11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222'
    positive, negative = '33333333-3333-3333-3333-333333333333', '44444444-4444-4444-4444-444444444444'
    reference = {'document_id': document, 'revision_id': revision, 'chunk_index': 1}
    cases = []
    for split, count in (('calibration', 100), ('holdout', 100), ('regression', 145)):
        for index in range(count):
            relevant = index < count // 2
            cases.append({'id': split + str(index), 'question': 'Private fictional question ' + split + str(index),
                'domain': 'fictional', 'intent_group': split + '-family-' + str(index // 2),
                'split': split, 'relevant': relevant, 'critical': index == 0, 'top1_required': index == 0,
                'unit_ids': ['unit'] if relevant else [], 'wording': ('direct' if index % 2 == 0 else 'paraphrase')
                    if relevant else 'control', 'required_groups': [[reference]] if relevant else [],
                'candidate_labels': {positive: True, negative: False} if relevant else {negative: False}})
    dataset = {'schema_version': 1, 'reviewed': True, 'review_notes': 'PRIVATE REVIEW NOTES',
               'required_units': ['unit'], 'cases': cases}
    results = {'baseline': [], 'semantic': []}
    for case in cases:
        for arm in results:
            matches = [{'document_id': document, 'revision_id': revision, 'chunk_index': 2,
                'chunk_id': negative, 'rerank_score': 2.0 if arm == 'baseline' and case['relevant'] else -1.0,
                'content': 'Fictional unrelated text.'}]
            if case['relevant']:
                matches.append({'document_id': document, 'revision_id': revision, 'chunk_index': 1,
                    'chunk_id': positive, 'rerank_score': 1.0, 'content': 'Fictional source evidence.'})
            matches.sort(key=lambda m: -m['rerank_score'])
            results[arm].append({'matches': matches, 'candidate_order': matches, 'status': 'matches_found'})
    scoring = {'version': 'passage-logistic-v1', 'feature_names': list(FEATURES), 'means': [0.0] * 8,
               'scales': [1.0] * 8, 'weights': [1.0] + [0.0] * 7, 'bias': 0.0}
    scoring['digest'] = scoring_digest(scoring)
    semantic = {'schema_version': 1, 'reviewed': True, 'review_notes': 'PRIVATE LEXICON NOTES',
        'package_fingerprint': '5' * 64, 'concepts': [{'id': 'fictional', 'canonical': 'fictional',
        'aliases': ['example'], 'requires_any': [], 'excludes_any': []}]}
    training = {'training_only': True, 'accepted_profile': False, 'holdout_evaluated': False,
        'scoring_digest': scoring['digest'], 'dataset_sha256': '6' * 64,
        'development_intent_groups': ['development-family'],
        'development_case_ids': ['development-' + str(i) for i in range(300)]}
    paths = {'report': private / 'full.json', 'dataset': private / 'dataset.json',
        'review': private / 'review.json', 'profile': private / 'full.semantic.profile.json',
        'semantic_artifact': root / 'artifacts/semantics/lexicon.json',
        'training_report': private / 'training.json'}
    write(paths['dataset'], dataset)
    write(paths['semantic_artifact'], semantic)
    write(paths['training_report'], training)
    write(paths['review'], {'human_approved': True, 'dataset_sha256': sha(paths['dataset'].read_bytes()),
        'source_evidence_checked': True, 'candidate_labels_checked': True, 'split_independence_checked': True,
        'review_statement': 'PRIVATE ACTUAL REVIEW DECLARATION'})
    inventory = {'schema_version': 1, 'models': {
        'e5': {'id': 'intfloat/multilingual-e5-small', 'revision': 'a' * 40,
               'root': 'models/embeddings/multilingual-e5-small/' + 'a' * 40, 'files_sha256': 'b' * 64},
        'reranker': {'id': 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1', 'revision': 'c' * 40,
                    'root': 'models', 'files_sha256': 'd' * 64}}, 'assets': [{
        'path': paths['semantic_artifact'].relative_to(root).as_posix(), 'group': 'semantics',
        'sha256': sha(paths['semantic_artifact'].read_bytes()), 'size_bytes': paths['semantic_artifact'].stat().st_size}]}
    write(root / 'artifacts/runtime-assets.json', inventory)
    pipelines = {arm: {'fingerprint': ('e' if arm == 'baseline' else 'f') * 64,
        **summarize(cases, values, [0.1] * 100, 0.0)} for arm, values in results.items()}
    report = {'schema_version': 1, 'collect_only': False, 'rollout_ready': True,
        'database_and_packages_unchanged': True, 'dataset_sha256': sha(paths['dataset'].read_bytes()),
        'corpus_fingerprint': '7' * 64, 'publication_snapshot': '8' * 64,
        'model_files_sha256': inventory['models']['e5']['files_sha256'], 'pipelines': pipelines,
        'comparisons': {'semantic_vs_baseline': compare(pipelines['baseline'], pipelines['semantic'])},
        'scoring': scoring, 'platform': 'PRIVATE MACHINE PATH',
        'artifacts': {'0': sha(paths['semantic_artifact'].read_bytes()), '1': '9' * 64,
                      '2': sha(paths['training_report'].read_bytes())},
        'release_configuration': {'semantic_mode': 'expand', 'intent_mode': 'off', 'candidate_count': 30,
            'batch_size': 4, 'model_revision': 'a' * 40, 'rerank_model_revision': 'c' * 40,
            'semantic_artifact_sha256': sha(paths['semantic_artifact'].read_bytes()),
            'scoring_digest': scoring['digest'], 'training_report_sha256': sha(paths['training_report'].read_bytes())}}
    write(paths['report'], report)
    write(paths['profile'], {'schema_version': 3, 'fingerprint': pipelines['semantic']['fingerprint'],
        'corpus_fingerprint': report['corpus_fingerprint'], 'threshold': 0.0, 'holdout_passed': True, 'scoring': scoring})
    return paths


def checker(*, root, manifest):
    data = json.loads((root / manifest).read_bytes())
    errors = []
    for entry in data['assets']:
        path = root / entry['path']
        if not path.is_file() or sha(path.read_bytes()) != entry['sha256'] or path.stat().st_size != entry['size_bytes']:
            errors.append({'code': 'fixture_integrity_mismatch'})
    return {'files': len(data['assets']), 'errors': errors}, bool(errors)


def promote(root, paths, verifier=checker, **kwargs):
    return promote_profile(**paths, version='semantic-score-v3-001', root=root, verify_inventory=verifier, **kwargs)


def test_copy_is_exact_inventory_preserved_and_manifest_excludes_private_inputs(tmp_path):
    paths = fixture(tmp_path)
    before = {key: path.read_bytes() for key, path in paths.items()}
    old_inventory = json.loads((tmp_path / 'artifacts/runtime-assets.json').read_bytes())
    result = promote(tmp_path, paths)
    target = tmp_path / result['profile']
    assert result['status'] == 'promoted'
    assert target.read_bytes() == before['profile']
    assert sha(target.read_bytes()) == result['profile_sha256']
    updated = json.loads((tmp_path / 'artifacts/runtime-assets.json').read_bytes())
    assert updated['models'] == old_inventory['models']
    assert updated['assets'][:-2] == old_inventory['assets']
    assert updated['assets'][-2]['path'] == result['profile']
    manifest_raw = (tmp_path / result['manifest']).read_text()
    assert 'PRIVATE' not in manifest_raw and 'Private fictional question' not in manifest_raw
    assert str(tmp_path) not in manifest_raw
    manifest = json.loads(manifest_raw)
    assert manifest['release_version'] == 'semantic-score-v3-001'
    assert manifest['runtime_release_accepted'] is True
    assert manifest['api_deployed'] is False
    assert manifest['evaluation']['human_review_sha256'] == sha(before['review'])
    assert all(path.read_bytes() == before[key] for key, path in paths.items())
    assert not (tmp_path / '.tmp/rerank-promotion.lock').exists()
    with pytest.raises(PromotionError, match='expected_current_sha256_required'):
        promote(tmp_path, paths)
    assert target.read_bytes() == before['profile']


@pytest.mark.parametrize('file,mutate', [
    ('report', lambda d: d.update(collect_only=True)),
    ('report', lambda d: d.update(rollout_ready=False)),
    ('report', lambda d: d.update(dataset_sha256='0' * 64)),
    ('report', lambda d: d.update(database_and_packages_unchanged=False)),
    ('report', lambda d: d['pipelines']['semantic']['gates'].update(no_irrelevant_returned=False)),
    ('report', lambda d: d['comparisons']['semantic_vs_baseline'].update(no_domain_evidence_regression=False)),
    ('report', lambda d: d['release_configuration'].update(intent_mode='assist')),
    ('report', lambda d: d['release_configuration'].update(model_revision='0' * 40)),
    ('report', lambda d: d['release_configuration'].update(scoring_digest='0' * 64)),
    ('report', lambda d: d['release_configuration'].update(semantic_artifact_sha256='0' * 64)),
    ('report', lambda d: d['release_configuration'].update(training_report_sha256='0' * 64)),
    ('report', lambda d: d['artifacts'].update({'2': '0' * 64})),
    ('review', lambda d: d.update(human_approved=False)),
    ('review', lambda d: d.update(dataset_sha256='0' * 64)),
    ('profile', lambda d: d.update(threshold=0.5)),
    ('profile', lambda d: d.update(fingerprint='0' * 64)),
    ('profile', lambda d: d.update(corpus_fingerprint='0' * 64)),
    ('profile', lambda d: d.update(schema_version=2)),
    ('profile', lambda d: d.update(extra='untrusted')),
    ('training_report', lambda d: d.update(training_only=False)),
])
def test_failed_or_mismatched_inputs_never_write_version(tmp_path, file, mutate):
    paths = fixture(tmp_path)
    value = json.loads(paths[file].read_bytes())
    mutate(value)
    write(paths[file], value)
    original_inventory = (tmp_path / 'artifacts/runtime-assets.json').read_bytes()
    with pytest.raises(ValueError):
        promote(tmp_path, paths)
    assert not (tmp_path / 'artifacts/rerank').exists()
    assert (tmp_path / 'artifacts/runtime-assets.json').read_bytes() == original_inventory


def test_partial_promotion_preserves_concurrent_inventory_and_requires_reconciliation(tmp_path):
    paths = fixture(tmp_path)
    concurrent = b'{"concurrent_user_change":true}\n'
    calls = 0

    def changing_checker(*, root, manifest):
        nonlocal calls
        result = checker(root=root, manifest=manifest)
        calls += 1
        if calls == 2:
            (root / 'artifacts/runtime-assets.json').write_bytes(concurrent)
        return result

    with pytest.raises(PromotionError, match='partial_promotion_reconcile_required'):
        promote(tmp_path, paths, changing_checker)
    assert (tmp_path / 'artifacts/runtime-assets.json').read_bytes() == concurrent
    assert (tmp_path / 'artifacts/rerank/relevance-profile.json').read_bytes() == paths['profile'].read_bytes()
    assert (tmp_path / '.tmp/rerank-promotion.lock').exists()
    receipts = list((tmp_path / '.tmp').glob('rerank-promotion-*/reconciliation.json'))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_bytes())['status'] == 'partial_promotion_reconcile_required'


def test_profile_must_be_expected_report_sibling_and_paths_private(tmp_path):
    paths = fixture(tmp_path)
    paths['profile'] = paths['dataset']
    with pytest.raises(PromotionError, match='emitted_profile'):
        promote(tmp_path, paths)
    paths = fixture(tmp_path / 'other')
    root = tmp_path / 'other'
    paths['dataset'] = tmp_path / 'outside.json'
    with pytest.raises(PromotionError, match='outside_repository'):
        promote(root, paths)


def test_existing_lock_blocks_without_touching_artifacts(tmp_path):
    paths = fixture(tmp_path)
    (tmp_path / '.tmp/rerank-promotion.lock').write_text('existing uncertain operation')
    with pytest.raises(PromotionError, match='locked_reconcile'):
        promote(tmp_path, paths)
    assert not (tmp_path / 'artifacts/rerank').exists()


def test_link_component_rejected_before_loading_inputs(tmp_path, monkeypatch):
    paths = fixture(tmp_path)
    original = Path.lstat

    def linked(path, *args, **kwargs):
        if path == paths['profile']:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', linked)
    with pytest.raises(PromotionError, match='linked_path'):
        promote(tmp_path, paths)
    assert not (tmp_path / 'artifacts/rerank').exists()


def test_failed_inventory_preflight_cannot_start_promotion(tmp_path):
    paths = fixture(tmp_path)

    def failed(**kwargs):
        return {'files': 0, 'errors': [{'code': 'synthetic_missing_model'}]}, 1

    with pytest.raises(PromotionError, match='inventory_preflight'):
        promote(tmp_path, paths, failed)
    assert not (tmp_path / 'artifacts/rerank').exists()
    assert not (tmp_path / '.tmp/rerank-promotion.lock').exists()


def test_raw_profile_bytes_including_whitespace_are_copied_unchanged(tmp_path):
    paths = fixture(tmp_path)
    value = json.loads(paths['profile'].read_bytes())
    raw = (json.dumps(value, separators=(',', ':')) + ' \r\n\n').encode()
    paths['profile'].write_bytes(raw)
    result = promote(tmp_path, paths)
    assert (tmp_path / result['profile']).read_bytes() == raw


def test_replace_current_profile_requires_matching_hash_and_keeps_recovery_bytes(tmp_path):
    paths = fixture(tmp_path)
    first = promote(tmp_path, paths)
    target = tmp_path / first['profile']
    previous = target.read_bytes()
    old_inventory = (tmp_path / 'artifacts/runtime-assets.json').read_bytes()
    paths['profile'].write_bytes(previous + b'\n')
    for expected in ('0' * 64, 'invalid'):
        with pytest.raises(PromotionError, match='current_rerank_sha256_mismatch'):
            promote(tmp_path, paths, expected_current_sha256=expected)
        assert target.read_bytes() == previous
        assert (tmp_path / 'artifacts/runtime-assets.json').read_bytes() == old_inventory
    result = promote(tmp_path, paths, expected_current_sha256=sha(previous))
    assert target.read_bytes() == paths['profile'].read_bytes()
    assert result['profile'] == 'artifacts/rerank/relevance-profile.json'
    inventory = json.loads((tmp_path / 'artifacts/runtime-assets.json').read_bytes())
    assert len([e for e in inventory['assets'] if e['group'] == 'rerank']) == 2
    backups = list((tmp_path / '.tmp').glob('rerank-promotion-*/previous/relevance-profile.json'))
    assert len(backups) == 1 and backups[0].read_bytes() == previous
    assert (backups[0].parent / 'runtime-assets.json').read_bytes() == old_inventory


def test_expected_hash_cannot_create_a_missing_current_profile(tmp_path):
    paths = fixture(tmp_path)
    with pytest.raises(PromotionError, match='current_rerank_profile_missing'):
        promote(tmp_path, paths, expected_current_sha256='0' * 64)
    assert not (tmp_path / 'artifacts/rerank').exists()


def test_failed_replacement_quality_gate_preserves_current(tmp_path):
    paths = fixture(tmp_path)
    first = promote(tmp_path, paths)
    profile = (tmp_path / first['profile']).read_bytes()
    inventory = (tmp_path / 'artifacts/runtime-assets.json').read_bytes()
    report = json.loads(paths['report'].read_bytes())
    report['rollout_ready'] = False
    write(paths['report'], report)
    with pytest.raises(PromotionError, match='full_passing_evaluation_required'):
        promote(tmp_path, paths, expected_current_sha256=sha(profile))
    assert (tmp_path / first['profile']).read_bytes() == profile
    assert (tmp_path / 'artifacts/runtime-assets.json').read_bytes() == inventory
    assert not (tmp_path / '.tmp/rerank-promotion.lock').exists()


@pytest.mark.parametrize('failure_at', [1, 2, 3])
def test_failed_replacement_keeps_lock_and_complete_previous_snapshot(tmp_path, monkeypatch, failure_at):
    from src.retrieval import rerank_promotion as module
    paths = fixture(tmp_path)
    first = promote(tmp_path, paths)
    old = {name: (tmp_path / 'artifacts/rerank' / name).read_bytes()
           for name in ('relevance-profile.json', 'manifest.json')}
    inventory = (tmp_path / 'artifacts/runtime-assets.json').read_bytes()
    paths['profile'].write_bytes(old['relevance-profile.json'] + b'\n')
    original = module.os.replace
    calls = 0

    def interrupted(source, target):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError('fictional interrupted replacement')
        original(source, target)

    monkeypatch.setattr(module.os, 'replace', interrupted)
    with pytest.raises(PromotionError, match='partial_promotion_reconcile_required'):
        promote(tmp_path, paths, expected_current_sha256=first['profile_sha256'])
    assert (tmp_path / '.tmp/rerank-promotion.lock').exists()
    receipts = list((tmp_path / '.tmp').glob('rerank-promotion-*/reconciliation.json'))
    assert len(receipts) == 1
    previous = receipts[0].parent / 'previous'
    assert all((previous / name).read_bytes() == raw for name, raw in old.items())
    assert (previous / 'runtime-assets.json').read_bytes() == inventory
    assert (tmp_path / 'artifacts/runtime-assets.json').read_bytes() == inventory
    with pytest.raises(PromotionError, match='locked_reconcile'):
        promote(tmp_path, paths, expected_current_sha256=first['profile_sha256'])
