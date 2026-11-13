"""Fictional offline evaluation tests; no model, network, or database."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from scripts import answer_evaluation_contract as contract
from scripts import evaluate_answers as cli
from scripts.answer_evaluation_runtime import LocalRuntime, evaluate_question


@pytest.fixture
def sources():
    return {'S1': {'content': 'Only permanent employees receive 100 credits.'}}


@pytest.fixture
def draft():
    return {'status': 'answered', 'claims': [{'text': 'Permanent employees receive 100 credits.',
            'citations': [{'source_id': 'S1', 'quote': 'Only permanent employees receive 100 credits.'}]}],
            'limitations': []}


@pytest.fixture
def verdict():
    return {'checks': [{'claim_index': 0, 'supported': True, 'conditions_preserved': True}],
            'status_appropriate': True, 'missing_parts_disclosed': True, 'no_unresolved_conflict': True}


def test_citations_must_exist_and_match_evidence(draft, sources):
    assert contract.validate_draft(draft, sources).status == 'answered'
    draft['claims'][0]['citations'][0]['source_id'] = 'invented'
    with pytest.raises(ValueError, match='invalid_citation'):
        contract.validate_draft(draft, sources)
    draft['claims'][0]['citations'][0] = {'source_id': 'S1', 'quote': 'Everyone receives 200 credits.'}
    with pytest.raises(ValueError, match='invalid_citation'):
        contract.validate_draft(draft, sources)


def test_partial_requires_limitations_and_verifier_covers_each_claim(draft, sources, verdict):
    draft['status'] = 'partial'
    with pytest.raises(ValueError, match='missing_limitations'):
        contract.validate_draft(draft, sources)
    draft['limitations'] = ['Required paperwork is not established.']
    parsed = contract.validate_draft(draft, sources)
    assert contract.accepted(parsed, verdict)
    verdict['checks'] *= 2
    assert not contract.accepted(parsed, verdict)
    verdict['checks'] = []
    assert not contract.accepted(parsed, verdict)


@pytest.mark.parametrize('field', ['status_appropriate', 'missing_parts_disclosed', 'no_unresolved_conflict'])
def test_verifier_failure_never_publishes(draft, sources, verdict, field):
    verdict[field] = False
    assert not contract.accepted(contract.validate_draft(draft, sources), verdict)


def retrieval(matches=True, reason=None):
    return SimpleNamespace(retrieve=AsyncMock(return_value={
        'status': 'matches_found' if matches else 'no_matches',
        'matches': [{'content': 'Only permanent employees receive 100 credits.', 'chunk_id': 'fictional'}] if matches else [],
        'fallback_reason': reason, 'corpus_fingerprint': 'fixed-corpus'}))


def test_unassessed_retrieval_never_calls_generator():
    model = SimpleNamespace(complete=AsyncMock())
    with pytest.raises(ValueError, match='relevance_unavailable'):
        asyncio.run(evaluate_question('Question', retrieval(False, 'relevance_unavailable'), model))
    model.complete.assert_not_called()


def test_empty_retrieval_remains_distinct_from_error():
    model = SimpleNamespace(complete=AsyncMock())
    result = asyncio.run(evaluate_question('Question', retrieval(False, 'below_relevance_threshold'), model))
    assert result['reason'] == 'below_relevance_threshold'
    assert result['inference_calls'] == 0
    model.complete.assert_not_called()


def test_one_generation_one_verification_preserves_baseline(draft, verdict):
    model = SimpleNamespace(complete=AsyncMock(side_effect=[(draft, {}), (verdict, {})]))
    result = asyncio.run(evaluate_question('Question', retrieval(), model))
    assert result['accepted'] and result['status'] == 'answered'
    assert result['inference_calls'] == 2
    assert result['baseline'][0]['content'] == 'Only permanent employees receive 100 credits.'
    generation, verification = model.complete.call_args_list
    assert generation.args[1]['evidence'] == verification.args[1]['evidence']
    assert 'draft' not in generation.args[1]
    assert verification.args[1]['draft'] == draft


def test_failed_verifier_keeps_draft_offline_without_repair(draft, verdict):
    verdict['checks'][0]['conditions_preserved'] = False
    model = SimpleNamespace(complete=AsyncMock(side_effect=[(draft, {}), (verdict, {})]))
    result = asyncio.run(evaluate_question('Question', retrieval(), model))
    assert not result['accepted'] and result['reason'] == 'verification_failed'
    assert result['draft'] == draft  # Private evaluation artifact, never API output.
    assert model.complete.call_count == 2


def test_bad_draft_skips_verifier(draft):
    draft['claims'][0]['citations'][0]['quote'] = 'fabricated'
    model = SimpleNamespace(complete=AsyncMock(return_value=(draft, {})))
    result = asyncio.run(evaluate_question('Question', retrieval(), model))
    assert result['reason'] == 'invalid_draft' and not result['accepted']
    assert model.complete.call_count == 1


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, 'REPOSITORY_ROOT', tmp_path)
    package = tmp_path / 'chunks' / 'fictional'
    package.mkdir(parents=True)
    for name, content in {'chunks.json': json.dumps({'chunks': [{'id': 'c1'}]}),
                          'metadata.json': '{}', 'extraction.json': '{}', 'source.txt': 'Fictional policy'}.items():
        (package / name).write_text(content, encoding='utf-8')
    cases = []
    for split in ('development', 'holdout'):
        for category, count in contract.CATEGORIES.items():
            for index in range(count // 2):
                identity = f'{split}-{category}-{index}'
                cases.append({'id': identity, 'question': f'Fictional question {identity}?',
                              'intent_group': identity, 'split': split, 'category': category,
                              'expected_status': 'answered' if category == 'supported' else 'insufficient_evidence',
                              'critical': category == 'adversarial', 'expected': 'Source-reviewed expectation.',
                              'source_refs': [{'package': 'chunks/fictional', 'chunk_id': 'c1'}]})
    data = {'schema_version': 1, 'source_reviewed': True, 'cases': cases,
            'packages': {'chunks/fictional': {p.name: contract.sha_file(p) for p in package.iterdir()}}}
    path = tmp_path / '.tmp' / 'cases.json'
    contract.write_new(path, data)
    return path, data, package


def test_case_gate_rejects_leakage_and_stale_packages(manifest):
    path, data, package = manifest
    assert len(contract.load_cases(path)['cases']) == 60
    data['cases'][30]['intent_group'] = data['cases'][0]['intent_group']
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='leaks_across'):
        contract.load_cases(path)
    data['cases'][30]['intent_group'] = 'separate'
    path.write_text(json.dumps(data), encoding='utf-8')
    (package / 'source.txt').write_text('Changed source', encoding='utf-8')
    with pytest.raises(ValueError, match='stale_package'):
        contract.load_cases(path)


def test_case_gate_rejects_historical_and_unreviewed_data(manifest):
    path, data, _ = manifest
    data['source_reviewed'] = False
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='source_reviewed'):
        contract.load_cases(path)
    data['source_reviewed'] = True
    data['cases'] = data['cases'][:40]
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='60_cases'):
        contract.load_cases(path)


def gate_inputs(data):
    rows = [{**{k: c[k] for k in ('id', 'split', 'category', 'critical')}, 'accepted': True,
             'status': 'answered', 'error': None, 'seconds': 20.0} for c in data['cases']]
    report = {'cases': rows, 'peak_system_memory_percent': 70, 'runtime_measured': True}
    reviews = {'user_accepted': True, 'cases': [{'id': r['id'], 'correct_and_complete': True,
        'no_unsupported_claims': True, 'sources_valid': True, 'critical_passed': True,
        'more_useful_than_excerpts': True} for r in rows]}
    return report, reviews


def test_quality_gate_requires_user_acceptance_and_all_supported_cases_count(manifest):
    report, review = gate_inputs(manifest[1])
    assert contract.evaluate_gates(report, review)['integration_ready']
    review['user_accepted'] = False
    assert not contract.evaluate_gates(report, review)['integration_ready']
    review['user_accepted'] = True
    # Refusing every answer must not create a perfect groundedness score and pass.
    for row in report['cases']:
        row['accepted'] = False
    gates = contract.evaluate_gates(report, review)['gates']
    assert not gates['supported_recall'] and not gates['value']


@pytest.mark.parametrize('change', ['unsupported', 'slow', 'memory', 'critical', 'missing_review'])
def test_gates_cannot_hide_quality_or_resource_failures(manifest, change):
    report, review = gate_inputs(manifest[1])
    if change == 'unsupported':
        review['cases'][30]['no_unsupported_claims'] = False
    elif change == 'slow':
        for row in report['cases'][:4]:
            row['seconds'] = 111
    elif change == 'memory':
        report['peak_system_memory_percent'] = 86
    elif change == 'critical':
        review['cases'][-1]['critical_passed'] = False
    else:
        review['cases'].pop()
        with pytest.raises(ValueError, match='manual_review'):
            contract.evaluate_gates(report, review)
        return
    assert not contract.evaluate_gates(report, review)['integration_ready']


def test_outputs_cannot_overwrite_or_escape_private_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, 'REPOSITORY_ROOT', tmp_path)
    with pytest.raises(ValueError, match='outside_tmp'):
        contract.write_new(tmp_path / 'public.json', {})
    output = tmp_path / '.tmp' / 'result.json'
    contract.write_new(output, {'keep': True})
    with pytest.raises(FileExistsError):
        contract.write_new(output, {'keep': False})
    assert json.loads(output.read_text()) == {'keep': True}


def test_preflight_fails_closed_without_opening_model_or_database(monkeypatch):
    monkeypatch.setattr(cli, 'load_settings', lambda: SimpleNamespace(
        rerank=SimpleNamespace(enabled=True, profile_path=None)))
    args = SimpleNamespace(cases=None, runtime=None, model=None, runtime_sha256=None, model_sha256=None)
    result = cli.preflight(args)
    assert result['status'] == 'blocked' and result['integration_ready'] is False
    assert set(result['blockers']) == {'validated_retrieval_profile_missing',
        'source_reviewed_60_case_manifest_missing', 'runtime_file_missing', 'model_file_missing'}


def test_runtime_checks_context_before_inference_and_requests_schema(tmp_path):
    async def exercise(too_long):
        requests = []
        def handle(request):
            payload = json.loads(request.content)
            requests.append((request.url.path, payload))
            if request.url.path == '/apply-template':
                return httpx.Response(200, json={'prompt': 'Formatted conversation'})
            if request.url.path == '/tokenize':
                return httpx.Response(200, json={'tokens': list(range(8192 if too_long else 20))})
            if request.url.path == '/v1/chat/completions':
                return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
                     'message': {'content': '{"ok": true}'}}], 'usage': {'completion_tokens': 5}})
            raise AssertionError('Unexpected endpoint')
        runtime = LocalRuntime(tmp_path / 'server', tmp_path / 'model', 'runtime', 'model')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url='http://127.0.0.1:8099') as client:
            runtime.client = client
            if too_long:
                with pytest.raises(ValueError, match='context_limit'):
                    await runtime.complete('policy', {}, {'type': 'object'}, 512)
                assert len(requests) == 2
            else:
                result, usage = await runtime.complete('policy', {}, {'type': 'object'}, 512)
                assert result == {'ok': True} and usage['completion_tokens'] == 5
                payload = requests[-1][1]
                assert payload['stream'] is False and payload['chat_template_kwargs'] == {'enable_thinking': False}
                assert payload['response_format']['json_schema']['strict'] is True
    asyncio.run(exercise(False))
    asyncio.run(exercise(True))


def test_owned_runtime_is_terminated_on_cleanup(tmp_path):
    class Process:
        stopped = False
        def poll(self):
            return None if not self.stopped else 0
        def terminate(self):
            self.stopped = True
        def wait(self, timeout=None):
            assert self.stopped
    runtime = LocalRuntime(tmp_path / 'server', tmp_path / 'model', 'x', 'y')
    process = Process()
    runtime.process = process
    runtime.client = SimpleNamespace(aclose=AsyncMock())
    asyncio.run(runtime.close())
    assert process.stopped and runtime.process is None and runtime.client is None


def test_cancellation_does_not_launch_verification(draft):
    model = SimpleNamespace(complete=AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(evaluate_question('Question', retrieval(), model))
    assert model.complete.call_count == 1


def test_cli_blocks_without_inference_and_preserves_existing_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(contract, 'REPOSITORY_ROOT', tmp_path)
    monkeypatch.setattr(cli, 'preflight', lambda args: {'status': 'blocked', 'blockers': ['missing_profile'],
                                                     'integration_ready': False})
    monkeypatch.setattr(cli, 'load_settings', lambda: SimpleNamespace(model_path=tmp_path / 'embedding',
                       rerank=SimpleNamespace(model_path=tmp_path / 'reranker')))
    experiment = AsyncMock()
    monkeypatch.setattr(cli, 'run_experiment', experiment)
    output = tmp_path / '.tmp' / 'experiment'
    assert cli.main(['run', '--output', str(output)]) == 1
    saved = (output / 'preflight.json').read_bytes()
    assert cli.main(['run', '--output', str(output)]) == 2
    assert (output / 'preflight.json').read_bytes() == saved
    experiment.assert_not_called()


def test_assess_binds_review_and_samples_to_completed_report(manifest, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, 'load_settings', lambda: SimpleNamespace(model_path=tmp_path / 'embedding',
                       rerank=SimpleNamespace(model_path=tmp_path / 'reranker')))
    report, reviews = gate_inputs(manifest[1])
    report['status'] = 'awaiting_manual_review'
    directory = tmp_path / '.tmp' / 'run'
    contract.write_new(directory / 'samples.private.json', [{'draft': 'fictional'}])
    report['samples_sha256'] = contract.sha_file(directory / 'samples.private.json')
    contract.write_new(directory / 'report.json', report)
    reviews['report_sha256'] = contract.sha_file(directory / 'report.json')
    contract.write_new(directory / 'review.json', reviews)
    args = ['assess', '--report', str(directory / 'report.json'), '--review', str(directory / 'review.json')]
    assert cli.main([*args, '--output', str(directory / 'decision.json')]) == 0
    (directory / 'samples.private.json').write_text('[]', encoding='utf-8')
    assert cli.main([*args, '--output', str(directory / 'changed.json')]) == 2
    assert not (directory / 'changed.json').exists()


def test_output_must_not_change_model_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'load_settings', lambda: SimpleNamespace(model_path=tmp_path / 'embedding',
                       rerank=SimpleNamespace(model_path=tmp_path / 'reranker')))
    args = SimpleNamespace(model=tmp_path / 'generation' / 'model.gguf', runtime=None)
    for directory in ('embedding', 'reranker', 'generation'):
        with pytest.raises(ValueError, match='output_inside_model'):
            cli.protect_artifact_directories(tmp_path / directory / 'new-output', args)


@pytest.fixture
def experiment_batch(manifest, draft, verdict, tmp_path, monkeypatch):
    """Real batch and question orchestration with fictional model/storage boundaries."""
    import copy
    import psutil
    from scripts import evaluate_retrieval as evaluation
    from src.ingestion import approved_publication, embeddings
    path, data, _ = manifest
    profile = tmp_path / '.tmp' / 'profile.json'
    profile.write_text('{}', encoding='utf-8')
    settings = SimpleNamespace(model_path=tmp_path / 'e5', model_revision='a' * 40,
                               rerank=SimpleNamespace(profile_path=profile))
    service = retrieval()
    service.start, service.close = AsyncMock(), AsyncMock()
    runtime = SimpleNamespace(start=AsyncMock(), close=AsyncMock(),
        verify_files=Mock(return_value={'runtime': 'fictional', 'model': 'fictional'}),
        complete=AsyncMock(side_effect=[({'ok': True}, {})] +
            [copy.deepcopy(value) for _ in data['cases'] for value in ((draft, {}), (verdict, {}))]))
    database = AsyncMock(return_value={'run_id': 'fictional-run'})
    closed = AsyncMock()
    monkeypatch.setattr(cli, 'REPOSITORY_ROOT', tmp_path)
    monkeypatch.setattr(cli, 'load_settings', lambda: settings)
    monkeypatch.setattr(cli, 'RetrievalService', lambda **kw: service)
    monkeypatch.setattr(cli, 'LocalRuntime', lambda *a, **kw: runtime)
    monkeypatch.setattr(cli, 'close_database', closed)
    monkeypatch.setattr(evaluation, 'database_state', database)
    monkeypatch.setattr(evaluation.packages, 'load_package', lambda *a, **kw:
        ({'revision_id': 'fictional-revision'}, {}, {'metadata.json': b'{}'}))
    monkeypatch.setattr(approved_publication, 'configuration', lambda *a: {})
    monkeypatch.setattr(embeddings, 'model_fingerprint', lambda *a: 'fictional-embedding')
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(percent=70., total=16 * 1024**3))
    monkeypatch.setattr(psutil, 'cpu_count', lambda **kw: 6 if kw.get('logical') is False else 12)
    args = SimpleNamespace(cases=path, runtime=tmp_path / 'server', model=tmp_path / 'model',
                           runtime_sha256='fictional', model_sha256='fictional', port=8099)
    return SimpleNamespace(args=args, data=data, profile=profile, service=service, runtime=runtime,
                           database=database, closed=closed)


async def run_batch_without_leaked_tasks(batch):
    """Memory sampling must finish before the batch returns or raises."""
    before = asyncio.all_tasks()
    try:
        return await cli.run_experiment(batch.args)
    finally:
        assert asyncio.all_tasks() <= before
        batch.runtime.close.assert_awaited_once()
        batch.service.close.assert_awaited_once()
        batch.closed.assert_awaited_once()


def test_experiment_batch_completes_private_samples_without_granting_acceptance(experiment_batch):
    batch = experiment_batch
    report, samples = asyncio.run(run_batch_without_leaked_tasks(batch))
    assert report['status'] == 'awaiting_manual_review' and report['error'] is None
    assert report['integration_ready'] is False and report['runtime_measured'] is True
    assert len(report['cases']) == len(samples) == 60
    assert all(row['error'] is None and row['accepted'] for row in report['cases'])
    assert [row['id'] for row in report['cases']] == [c['id'] for c in batch.data['cases']]
    assert batch.runtime.complete.await_count == 121  # One warm-up, then one draft/verifier per case.
    assert batch.service.retrieve.await_count == 62  # Probe, cases and final corpus check.
    assert batch.database.await_count == 2
    assert report['peak_system_memory_percent'] == 70.
    serialized = json.dumps(report)
    assert batch.data['cases'][0]['question'] not in serialized
    assert 'Only permanent employees' not in serialized
    assert samples[0]['draft']['claims'] and samples[0]['baseline']


@pytest.mark.parametrize('failure', ['calibration', 'startup', 'warmup_timeout', 'timeout', 'inference'])
def test_experiment_stops_batch_and_drains_resources(experiment_batch, failure):
    batch = experiment_batch
    if failure == 'calibration':
        batch.service.retrieve.return_value = {'status': 'no_matches', 'matches': [],
            'fallback_reason': 'relevance_unavailable', 'corpus_fingerprint': 'fixed-corpus'}
    elif failure == 'startup':
        batch.runtime.start.side_effect = RuntimeError('PRIVATE RUNTIME DIAGNOSTIC')
    else:
        batch.runtime.complete.side_effect = ([TimeoutError('PRIVATE')] if failure == 'warmup_timeout' else
            [({'ok': True}, {}), TimeoutError('PRIVATE') if failure == 'timeout' else RuntimeError('PRIVATE')])
    report, samples = asyncio.run(run_batch_without_leaked_tasks(batch))
    expected = {'calibration': 'runtime_or_retrieval_preflight_failed',
        'startup': 'runtime_or_retrieval_preflight_failed', 'warmup_timeout': 'preflight_or_warmup_timeout',
        'timeout': 'answer_timeout', 'inference': 'experiment_failed'}[failure]
    assert report['status'] == 'failed' and report['error'] == expected
    assert not report['integration_ready'] and not samples
    assert len(report['cases']) == (1 if failure in ('timeout', 'inference') else 0)
    assert 'PRIVATE' not in json.dumps(report)
    if failure == 'calibration':
        batch.runtime.start.assert_not_awaited()
        batch.runtime.complete.assert_not_awaited()
    elif failure in ('timeout', 'inference'):
        assert batch.runtime.complete.await_count == 2  # No verifier or subsequent case.


@pytest.mark.parametrize('drift', ['corpus_during_case', 'corpus_after_cases', 'storage',
                                 'cases', 'profile', 'embedding', 'runtime'])
def test_experiment_rejects_changed_inputs_or_publications(experiment_batch, monkeypatch, drift):
    from src.ingestion import embeddings
    batch = experiment_batch
    if drift.startswith('corpus'):
        original = batch.service.retrieve.return_value
        changed = dict(original, corpus_fingerprint='changed')
        batch.service.retrieve.side_effect = ([original, changed] if drift == 'corpus_during_case' else
                                             [original] * 61 + [changed])
    elif drift == 'storage':
        batch.database.side_effect = [{'run_id': 'fictional-run'}, {'run_id': 'changed'}]
    elif drift == 'embedding':
        fingerprints = iter(['fictional-embedding', 'changed'])
        monkeypatch.setattr(embeddings, 'model_fingerprint', lambda *a: next(fingerprints))
    elif drift == 'runtime':
        batch.runtime.verify_files.side_effect = [{'runtime': 'fictional', 'model': 'fictional'}, {'changed': True}]
    else:
        async def mutate():
            path = batch.args.cases if drift == 'cases' else batch.profile
            path.write_text(path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        batch.runtime.start.side_effect = mutate
    report, _ = asyncio.run(run_batch_without_leaked_tasks(batch))
    expected = {'corpus_during_case': 'experiment_failed', 'corpus_after_cases': 'evaluation_corpus_changed',
        'storage': 'evaluation_storage_changed', 'embedding': 'embedding_model_changed',
        'cases': 'evaluation_inputs_changed', 'profile': 'evaluation_inputs_changed',
        'runtime': 'evaluation_inputs_changed'}[drift]
    assert report['status'] == 'failed' and report['error'] == expected
    assert not report['integration_ready']


@pytest.mark.parametrize('stage', ['runtime', 'retrieval'])
def test_experiment_cleanup_failure_still_closes_remaining_resources(experiment_batch, stage):
    batch = experiment_batch
    target = batch.runtime if stage == 'runtime' else batch.service
    target.close.side_effect = RuntimeError('fictional cleanup failure')
    with pytest.raises(RuntimeError, match='fictional cleanup failure'):
        asyncio.run(run_batch_without_leaked_tasks(batch))


def test_experiment_cancellation_drains_resources_before_propagating(experiment_batch):
    batch = experiment_batch
    batch.runtime.complete.side_effect = [({'ok': True}, {}), asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_batch_without_leaked_tasks(batch))
    assert batch.runtime.complete.await_count == 2
