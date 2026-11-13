"""Synthetic evaluator contracts; no real documents, model or database."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

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


@pytest.fixture
def evaluator_batch(tmp_path, monkeypatch):
    """Exercise the real evaluator loop, replacing only model/package/DB boundaries."""
    payload = {'chunks': [{'content': 'Fictional overview'}, {'content': 'Fictional allowance'}]}
    files = {'metadata.json': b'{}', 'chunks.json': b'fictional'}
    cases_path = tmp_path / 'cases.json'
    cases_path.write_text(json.dumps({'revision_id': 'revision',
        'chunks_sha256': evaluation.packages.canonical_hash(payload['chunks']),
        'cases': [case([[1]])]}), encoding='utf-8')
    service = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), retrieve=AsyncMock())
    state = AsyncMock(return_value={'run_id': 'run'})
    closed = AsyncMock()
    monkeypatch.setattr(evaluation, 'RetrievalService', lambda **kw: service)
    monkeypatch.setattr(evaluation, 'database_state', state)
    monkeypatch.setattr(evaluation, 'close_database', closed)
    monkeypatch.setattr(evaluation, 'model_fingerprint', lambda *_: 'model')
    monkeypatch.setattr(evaluation, 'configuration', lambda *_: {})
    monkeypatch.setattr(evaluation.packages, 'load_package', lambda *a, **kw:
                        ({'revision_id': 'revision'}, payload, files))
    monkeypatch.setattr(evaluation.packages, 'snapshot_files', lambda *_: files)
    args = SimpleNamespace(package=tmp_path / 'package', cases=cases_path,
                           model_path=tmp_path / 'model', model_revision='a' * 40, top_k=5)
    return args, service, state, closed


@pytest.mark.parametrize('outcome', ['accepted', 'wrong_revision', 'wrong_run',
                                   'relevance_unavailable', 'below_relevance_threshold'])
def test_evaluator_scores_only_public_filtered_evidence(evaluator_batch, outcome):
    from src.database.runtime import run_async
    args, service, state, closed = evaluator_batch
    matches = [] if outcome in ('relevance_unavailable', 'below_relevance_threshold') else [
        {**match(1, revision='other' if outcome == 'wrong_revision' else 'revision',
                   run='old' if outcome == 'wrong_run' else 'run'),
         'cosine_distance': .2, 'rerank_score': 3.0, 'content': 'PRIVATE FICTIONAL EXCERPT'}]
    service.retrieve.return_value = {'status': 'matches_found' if matches else 'no_matches',
        'matches': matches, 'fallback_reason': None if matches else outcome}
    report = run_async(evaluation.evaluate(args))
    assert report['summary']['target_met'] is (outcome == 'accepted')
    assert report['summary']['primary_total'] == 1
    assert report['cases'][0]['evidence_complete'] is (outcome == 'accepted')
    assert report['cases'][0]['group_ranks'] == ([1] if outcome == 'accepted' else [None])
    assert 'PRIVATE FICTIONAL EXCERPT' not in json.dumps(report)
    service.retrieve.assert_awaited_once_with('What is the allowance?', top_k=5)
    assert state.await_count == 2
    service.close.assert_awaited_once()
    closed.assert_awaited_once()


@pytest.mark.parametrize('failure', ['storage', 'package', 'model', 'cases', 'retrieve'])
def test_evaluator_rejects_drift_and_closes_resources(evaluator_batch, monkeypatch, failure):
    from src.database.runtime import run_async
    args, service, state, closed = evaluator_batch
    service.retrieve.return_value = {'status': 'no_matches', 'matches': []}
    if failure == 'storage':
        state.side_effect = [{'run_id': 'run'}, {'run_id': 'changed'}]
    elif failure == 'package':
        monkeypatch.setattr(evaluation.packages, 'snapshot_files', lambda *_: {'changed': b'bytes'})
    elif failure == 'model':
        fingerprints = iter(['model', 'changed'])
        monkeypatch.setattr(evaluation, 'model_fingerprint', lambda *_: next(fingerprints))
    elif failure == 'cases':
        async def retrieve(*a, **kw):
            args.cases.write_text(args.cases.read_text(encoding='utf-8') + '\n', encoding='utf-8')
            return {'status': 'no_matches', 'matches': []}
        service.retrieve.side_effect = retrieve
    else:
        service.retrieve.side_effect = RuntimeError('fictional dependency failure')
    with pytest.raises(RuntimeError if failure == 'retrieve' else ValueError,
                       match='fictional dependency failure' if failure == 'retrieve' else 'evaluation_inputs_changed'):
        run_async(evaluation.evaluate(args))
    service.close.assert_awaited_once()
    closed.assert_awaited_once()
