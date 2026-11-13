"""Synthetic evaluator contracts; no real documents, model or database."""
import json
from types import SimpleNamespace

import pytest
from scripts import evaluate_retrieval as evaluation


def match(index, revision='revision', run='run'):
    return {'chunk_index': index, 'revision_id': revision, 'run_id': run}


def case(groups, category='detail'):
    return {'id': 'synthetic', 'category': category, 'question': 'What is the allowance?',
            'required_groups': groups, 'critical': True}


def test_inventory_is_not_detail_and_wrong_revision_or_run_does_not_count():
    expected = case([[1]])
    for matches in ([match(0)], [match(1, revision='other')], [match(1, run='old')]):
        assert evaluation.grade(expected, matches, 'revision', 'run')['evidence_complete'] is False
    result = evaluation.grade(expected, [match(0), match(1)], 'revision', 'run')
    assert result['evidence_complete'] is True
    assert result['group_ranks'] == [2]


def test_multichunk_evidence_requires_every_group_but_accepts_alternatives():
    expected = case([[1, 2], [3]])
    assert not evaluation.grade(expected, [match(2)], 'revision', 'run')['evidence_complete']
    assert evaluation.grade(expected, [match(2), match(3)], 'revision', 'run')['evidence_complete']


def test_unsupported_is_neither_a_pass_nor_a_failure_even_with_matches():
    result = evaluation.grade(case([], 'unsupported'), [match(0)], 'revision', 'run')
    assert result['evidence_complete'] is None
    assert result['manual_review_required']
    assert evaluation.summarize([result])['primary_total'] == 0
    assert not evaluation.summarize([result])['target_met']


def test_cases_pin_metadata_revision_and_chunk_bytes(tmp_path):
    record = {'revision_id': 'revision'}
    payload = {'chunks': [{'content': 'Synthetic inventory'}, {'content': 'Synthetic detail'}]}
    data = {'revision_id': 'revision', 'chunks_sha256': evaluation.packages.canonical_hash(payload['chunks']),
            'cases': [case([[1]])]}
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    assert evaluation.load_cases(path, record, payload) == data['cases']
    with pytest.raises(ValueError, match='stale_cases'):
        evaluation.load_cases(path, {'revision_id': 'directory-name-is-not-revision'}, payload)
    with pytest.raises(ValueError, match='stale_cases'):
        evaluation.load_cases(path, record, {'chunks': [{'content': 'Changed'}]})
    data['cases'][0]['required_groups'] = [[2]]
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='invalid_chunk_index'):
        evaluation.load_cases(path, record, payload)


@pytest.mark.parametrize('changed', ['package_sha256', 'model_files_sha256', 'content_hash'])
def test_active_run_fingerprint_drift_rejected(changed):
    expected = {'model_name': 'synthetic', 'model_revision': 'a' * 40,
                'package_sha256': 'package', 'model_files_sha256': 'model', 'content_hash': 'content'}
    run = SimpleNamespace(is_active=True, status='succeeded', chunk_count=2, embedding_dimensions=384,
                          model_name='synthetic', model_revision='a' * 40, configuration=dict(expected))
    evaluation.check_run(run, expected, 2)
    run.configuration[changed] = 'old'
    with pytest.raises(ValueError, match='active_run_mismatch'):
        evaluation.check_run(run, expected, 2)


def test_output_must_resolve_below_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, 'REPOSITORY_ROOT', tmp_path)
    assert evaluation.output_path(tmp_path / '.tmp' / 'report.json') == tmp_path / '.tmp' / 'report.json'
    for path in (tmp_path / 'report.json', tmp_path / '.tmp', tmp_path / '.tmp' / '..' / 'report.json'):
        with pytest.raises(ValueError, match='output_outside_tmp'):
            evaluation.output_path(path)


def test_critical_failure_blocks_target_despite_high_overall_rate():
    passed = {'category': 'detail', 'evidence_complete': True, 'critical': False}
    failed = {'category': 'condition', 'evidence_complete': False, 'critical': True}
    result = evaluation.summarize([dict(passed) for _ in range(19)] + [failed])
    assert result['primary_rate'] == .95
    assert not result['target_met']
