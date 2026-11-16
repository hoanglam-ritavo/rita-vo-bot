"""Synthetic preparation contracts; no model, database or quality acceptance."""
import json
from uuid import uuid4

import pytest

from src.cli.prepare_rerank import main
from src.ingestion import document_packages as packages
from src.retrieval import rerank_preparation as prep
from src.tests.package_fixtures import ready


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(packages.encoded(value))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'REPOSITORY_ROOT', tmp_path)
    package, _ = ready(tmp_path)
    record, payload, _ = packages.load_package(tmp_path, package, eligible=True)
    candidate = str(uuid4())
    cases = []
    for index in range(100):
        relevant = index % 2 == 0
        cases.append({'id': f'fictional-{index}', 'question': f'Fictional policy question {index}?',
            'category': 'detail' if relevant else 'unsupported', 'critical': False,
            'split': 'calibration' if index < 40 else 'holdout' if index < 80 else 'regression',
            'intent_group': f'fictional-family-{index}', 'relevant': relevant,
            'required_groups': [[0]] if relevant else []})
    data = {'revision_id': record['revision_id'],
            'chunks_sha256': packages.canonical_hash(payload['chunks']), 'cases': cases}
    case_path = tmp_path / '.tmp/source/cases.json'
    manifest = case_path.with_name('manifest.json')
    write(case_path, data)
    write(manifest, {'datasets': [{'package': package.relative_to(tmp_path).as_posix(),
                                  'cases': case_path.relative_to(tmp_path).as_posix()}]})
    return tmp_path, manifest, case_path, package, candidate


def init(workspace):
    root, manifest, _, _, _ = workspace
    assert main(['init', '--manifest', str(manifest), '--output-dir', '.tmp/prepared', '--json']) == 0
    return root / '.tmp/prepared'


def reviewed_collection(workspace, target):
    root, _, _, _, candidate = workspace
    manifest = target / 'manifest.json'
    _, items, entries, _ = prep.load_workspace(manifest)
    arguments = json.loads((target / 'receipt.json').read_bytes())['collection_arguments'][5:-3]
    report = {'collect_only': True, 'rollout_ready': False, 'database_and_packages_unchanged': True,
        'manifest_sha256': packages.sha(manifest.read_bytes()), 'preparation_binding': prep.binding(items),
        'reproduction_arguments': arguments, 'fingerprint': 'fictional-model',
        'corpus_fingerprint': 'fictional-corpus', 'cases': [
            {'id': case['id'], 'after_rerank': [{'chunk_id': candidate}]} for case, _, _ in entries]}
    collection = target / 'collection-01.json'
    write(collection, report)
    case_path = target / 'cases-000.json'
    data = json.loads(case_path.read_bytes())
    for case in data['cases']:
        case['candidate_labels'] = {candidate: case['relevant']}
    write(case_path, data)
    review = {key: True for key in ('reviewed', 'source_evidence_checked', 'split_independence_checked',
              'inspected_families_in_regression', 'candidate_labels_checked')}
    review['review_notes'] = 'Synthetic review declaration only.'
    write(target / 'review.json', review)
    return collection, case_path


def test_init_copies_without_changing_source_and_never_overwrites(workspace, capsys):
    root, manifest, case_path, _, _ = workspace
    before = {p: p.read_bytes() for p in (manifest, case_path)}
    target = init(workspace)
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert json.loads((target / 'cases-000.json').read_bytes()) == json.loads(case_path.read_bytes())
    receipt = json.loads((target / 'receipt.json').read_bytes())
    assert receipt['status'] == 'draft' and receipt['reviewed'] is False
    assert receipt['case_count'] == 100 and receipt['binding']
    assert json.loads((target / 'review.json').read_bytes())['reviewed'] is False
    assert main(['init', '--manifest', str(manifest), '--output-dir', str(target), '--json']) == 1
    assert 'Fictional policy question' not in capsys.readouterr().out


def test_validate_is_model_and_database_free_and_root_relative(workspace, monkeypatch, capsys):
    root, manifest, _, _, _ = workspace
    from src.ingestion import embeddings
    from src import database
    monkeypatch.setattr(embeddings, 'load_model', lambda *_: pytest.fail('model load'))
    monkeypatch.setattr(database, 'session_scope', lambda *_: pytest.fail('database access'))
    monkeypatch.chdir(root.parent)
    assert main(['validate', '--manifest', '.tmp/source/manifest.json', '--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['cases'] == 100 and result['model_database_verified'] is False
    assert main(['validate', '--manifest', str(manifest), '--stage', 'calibration', '--json']) == 1
    assert main(['validate', '--manifest', '.tmp/missing.json', '--json']) == 2


@pytest.mark.parametrize('mutation,code', [
    ('revision', 'stale_cases'), ('hash', 'stale_cases'), ('index', 'invalid_chunk_index'),
    ('duplicate', 'invalid_cases'), ('question', 'duplicate_or_empty_cases'),
    ('leak', 'intent_group_leaks_across_splits'), ('split', 'invalid_online_case'),
    ('null', 'complete_candidate_labels_required'), ('few', 'need_100_split_balanced_cases')])
def test_rejects_invalid_cases_with_safe_errors(workspace, mutation, code, capsys):
    _, manifest, case_path, _, candidate = workspace
    data = json.loads(case_path.read_bytes())
    stage = 'collection'
    if mutation == 'revision':
        data['revision_id'] = str(uuid4())
    elif mutation == 'hash':
        data['chunks_sha256'] = '0' * 64
    elif mutation == 'index':
        data['cases'][0]['required_groups'] = [[999]]
    elif mutation == 'duplicate':
        data['cases'][1]['id'] = data['cases'][0]['id']
    elif mutation == 'question':
        data['cases'][1]['question'] = data['cases'][0]['question']
    elif mutation == 'leak':
        data['cases'][45]['intent_group'] = data['cases'][0]['intent_group']
    elif mutation == 'split':
        data['cases'][0]['split'] = 'train'
    else:
        stage = 'calibration'
        for case in data['cases']:
            case['candidate_labels'] = {candidate: True}
        if mutation == 'null':
            data['cases'][0]['candidate_labels'][candidate] = None
        else:
            data['cases'] = data['cases'][:10]
    write(case_path, data)
    assert main(['validate', '--manifest', str(manifest), '--stage', stage, '--json']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['error']['code'] == code
    assert 'Fictional policy question' not in json.dumps(result)


def test_freeze_accepts_only_label_edits_and_records_reproduction(workspace):
    root, _, _, _, _ = workspace
    target = init(workspace)
    collection, _ = reviewed_collection(workspace, target)
    before = {p: p.read_bytes() for p in target.iterdir()}
    result = prep.freeze_workspace(target / 'manifest.json', collection, target / 'review.json', '.tmp/frozen')
    assert result['status'] == 'frozen'
    assert all(p.read_bytes() == raw for p, raw in before.items())
    receipt = json.loads((root / '.tmp/frozen/receipt.json').read_bytes())
    assert receipt['profile_accepted'] is False and receipt['input_hashes']
    assert '--collect-only' not in receipt['benchmark_arguments']
    assert not list((root / '.tmp/frozen').glob('*.profile.json'))


@pytest.mark.parametrize('mutation,code', [
    ('review', 'human_review_required'), ('question', 'collection_inputs_mismatch'),
    ('evidence', 'collection_inputs_mismatch'), ('split', 'collection_inputs_mismatch'),
    ('manifest', 'collection_inputs_mismatch'), ('package', 'collection_inputs_mismatch'),
    ('missing_label', 'complete_candidate_labels_required'), ('extra_label', 'complete_candidate_labels_required'),
    ('duplicate_candidate', 'complete_candidate_labels_required'), ('old_report', 'collection_inputs_mismatch'),
    ('config', 'collection_reproduction_missing')])
def test_freeze_rejects_drift_and_incomplete_review(workspace, mutation, code):
    _, _, _, package, candidate = workspace
    target = init(workspace)
    collection, case_path = reviewed_collection(workspace, target)
    data = json.loads(case_path.read_bytes())
    report = json.loads(collection.read_bytes())
    if mutation == 'review':
        write(target / 'review.json', {'reviewed': True, 'review_notes': 'Insufficient.'})
    elif mutation == 'question':
        data['cases'][0]['question'] += ' Changed.'
    elif mutation == 'evidence':
        data['cases'][0]['required_groups'] = [[1]]
    elif mutation == 'split':
        data['cases'][0]['split'], data['cases'][40]['split'] = 'holdout', 'calibration'
    elif mutation == 'manifest':
        (target / 'manifest.json').write_bytes((target / 'manifest.json').read_bytes() + b'\n')
    elif mutation == 'package':
        meta = json.loads((package / 'metadata.json').read_bytes())
        meta['applicability'] = 'Changed fictional applicability'
        write(package / 'metadata.json', meta)
    elif mutation == 'missing_label':
        data['cases'][0]['candidate_labels'] = {}
    elif mutation == 'extra_label':
        data['cases'][0]['candidate_labels'][str(uuid4())] = False
    elif mutation == 'duplicate_candidate':
        report['cases'][0]['after_rerank'].append({'chunk_id': candidate})
    elif mutation == 'old_report':
        report.pop('preparation_binding')
    else:
        report['reproduction_arguments'].append('--collect-only')
    write(case_path, data)
    write(collection, report)
    with pytest.raises(prep.PreparationError) as error:
        prep.freeze_workspace(target / 'manifest.json', collection, target / 'review.json', '.tmp/frozen')
    assert error.value.detail['code'] == code
    assert not (target.parent / 'frozen').exists()


def test_output_and_input_boundaries_and_strict_json(workspace, capsys):
    root, manifest, _, _, _ = workspace
    assert main(['init', '--manifest', str(manifest), '--output-dir', str(root / 'tracked'), '--json']) == 1
    assert not (root / 'tracked').exists()
    manifest.write_text('{"datasets": [], "datasets": []}', encoding='utf-8')
    assert main(['validate', '--manifest', str(manifest), '--json']) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['error']['code'] == 'invalid_json'


def test_package_or_input_change_during_write_prevents_finalization(workspace, monkeypatch):
    root, manifest, case_path, _, _ = workspace
    original = prep.verify_unchanged
    calls = []
    def change(snapshots):
        calls.append(True)
        if len(calls) == 2:
            case_path.write_bytes(case_path.read_bytes() + b'\n')
        original(snapshots)
    monkeypatch.setattr(prep, 'verify_unchanged', change)
    with pytest.raises(prep.PreparationError, match='inputs_changed'):
        prep.init_workspace(manifest, '.tmp/during-write', [])
    assert not (root / '.tmp/during-write').exists()
