"""Fictional pipeline regressions; real binding grammar checks do not load weights."""
import asyncio
from copy import deepcopy
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.answering.config import AnswerSettings, from_environment
from src.answering.contracts import Draft, verdict_schema, verification_diagnostics
from src.answering.pipeline import evaluate_question
from src.answering.runtime import grammar_schema


def sample():
    sources = {'S1': {'content': 'PRIVATE Permanent staff get 100 credits with a certificate.',
        'title': 'PRIVATE title', 'applicability': 'Permanent staff', 'effective_from': None,
        'effective_to': None, 'version_label': 'v1',
        'origins': [{'chunk_id': 'PRIVATE identifier', 'source_path': 'PRIVATE path'}]}}
    draft = {'status': 'answered', 'claims': [{'text': sources['S1']['content'],
        'citations': [{'source_id': 'S1', 'quote': sources['S1']['content']}]}], 'limitations': []}
    verdict = {'checks': [{'claim_index': 0, 'supported': True, 'conditions_preserved': True}],
        'status_appropriate': True, 'missing_parts_disclosed': True, 'no_unresolved_conflict': True}
    return sources, draft, verdict


@pytest.mark.parametrize('count', [0, 1, 3, 12])
def test_verifier_schema_has_fixed_positions_and_native_grammar(count):
    schema = verdict_schema(count)
    checks = schema['properties']['checks']
    assert checks['minItems'] == checks['maxItems'] == count
    assert [item['properties']['claim_index']['const'] for item in checks['prefixItems']] == list(range(count))
    # The optional installed binding must compile the actual schema, without loading a model.
    backend = pytest.importorskip('llama_cpp')
    grammar = backend.LlamaGrammar.from_json_schema(json.dumps(grammar_schema(schema)), verbose=False)
    assert grammar is not None


@pytest.mark.parametrize('kind,expected', [
    ('schema', 'verdict_invalid_structure'), ('missing', 'claim_indices_mismatch'),
    ('extra', 'claim_indices_mismatch'), ('duplicate', 'claim_indices_mismatch'),
    ('index', 'claim_indices_mismatch'), ('supported', 'claim_unsupported'),
    ('conditions', 'conditions_missing'), ('status', 'status_inappropriate'),
    ('parts', 'missing_parts_undisclosed'), ('conflict', 'unresolved_conflict')])
def test_diagnostics_reject_each_failure(kind, expected):
    _, raw, verdict = sample()
    if kind == 'schema': verdict['status_appropriate'] = 'PRIVATE'
    elif kind == 'missing': verdict['checks'] = []
    elif kind == 'extra': verdict['checks'] += [{**verdict['checks'][0], 'claim_index': 1}]
    elif kind == 'duplicate': verdict['checks'] *= 2
    elif kind == 'index': verdict['checks'][0]['claim_index'] = 9
    elif kind == 'supported': verdict['checks'][0]['supported'] = False
    elif kind == 'conditions': verdict['checks'][0]['conditions_preserved'] = False
    else: verdict[{'status': 'status_appropriate', 'parts': 'missing_parts_disclosed',
                   'conflict': 'no_unresolved_conflict'}[kind]] = False
    assert expected in verification_diagnostics(Draft.model_validate(raw), verdict)


@pytest.mark.parametrize('debug', [False, True])
@pytest.mark.parametrize('reject', [False, True])
def test_compact_evidence_logging_and_no_retry(debug, reject, capsys, caplog):
    sources, raw, verdict = sample()
    if reject: verdict['checks'][0]['conditions_preserved'] = False
    record = deepcopy(sources['S1'])
    origin = record.pop('origins')[0]
    retrieval = SimpleNamespace(retrieve=AsyncMock(return_value={'status': 'matches_found',
        'matches': [{**record, **origin}], 'corpus_fingerprint': 'fictional', 'fallback_reason': None,
        'relevance': {'status': 'relevant_candidates', 'warnings': []}}))
    runtime = SimpleNamespace(complete=AsyncMock(side_effect=[
        (raw, {'prompt_tokens': 123, 'completion_tokens': 45, 'private': 'PRIVATE'}), (verdict, {})]))
    with caplog.at_level(logging.INFO):
        row = asyncio.run(evaluate_question('PRIVATE question', retrieval, runtime, debug_raw=debug,
                                            request_id='fictional-request'))
    assert row['accepted'] is not reject
    assert runtime.complete.await_count == 2
    for call in runtime.complete.await_args_list:
        evidence = call.args[1]['evidence']['S1']
        assert evidence == {key: val for key, val in sources['S1'].items() if key != 'origins'}
    assert row['sources']['S1']['origins'][0]['chunk_id'] == origin['chunk_id']
    assert 'PRIVATE' not in caplog.text
    assert 'fictional-request' in caplog.text and 'prompt_tokens=123' in caplog.text
    output = capsys.readouterr().out
    assert ('Raw draft generated:' in output) is debug
    assert ('Raw verdict generated:' in output) is debug
    assert ('PRIVATE' in output) is debug
    assert 'private' not in row['generation_usage']


@pytest.mark.parametrize('value,expected', [('true', True), ('TRUE', True), ('false', False)])
def test_debug_config(value, expected):
    assert from_environment({'ANSWER_DEBUG_RAW': value}).debug_raw is expected
    assert AnswerSettings().debug_raw is False


def test_debug_config_rejects_invalid():
    with pytest.raises(ValueError): from_environment({'ANSWER_DEBUG_RAW': 'yes'})
    with pytest.raises(ValueError): AnswerSettings(debug_raw='false')


def test_regression_manifest_and_comparison(tmp_path, monkeypatch):
    from scripts import benchmark_answer_pipeline as bench
    monkeypatch.setattr(bench, 'REPOSITORY_ROOT', tmp_path)
    root = tmp_path / '.tmp'
    root.mkdir()
    path = root / 'cases.json'
    path.write_text(json.dumps({'schema_version': 1, 'cases': [
        {'id': f'case-{i}', 'text': 'Fictional question'} for i in range(8)]}), encoding='utf-8')
    assert len(bench.load_cases(path)) == 8
    with pytest.raises(ValueError): bench.private_path(tmp_path / 'outside.json')
    baseline = dict(status='completed', cases_sha256='c', retrieval_identity='r', runtime_identity='m',
                    configuration={}, packages='p', corpora=['corpus'], summary={})
    assert bench.comparison(baseline, deepcopy(baseline))['comparable']
    for field in ('cases_sha256', 'retrieval_identity', 'runtime_identity', 'configuration', 'packages', 'corpora'):
        candidate = deepcopy(baseline)
        candidate[field] = ['changed'] if field == 'corpora' else 'changed'
        assert not bench.comparison(baseline, candidate)['comparable']


@pytest.mark.parametrize('failure', [False, True])
def test_regression_collection_stops_uncertain_outcomes_and_closes(tmp_path, monkeypatch, failure):
    from scripts import benchmark_answer_pipeline as bench
    from src.api.config import Settings
    monkeypatch.setattr(bench, 'REPOSITORY_ROOT', tmp_path)
    monkeypatch.setattr(bench, 'snapshot_fingerprint', lambda: 'packages')
    monkeypatch.setattr(bench, 'implementation_identity', lambda: 'code')
    output = tmp_path / '.tmp' / 'run'
    output.mkdir(parents=True)
    runtime = SimpleNamespace(verify_files=lambda: {'model': 'fictional'})
    service = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), retrieval=object(),
        answers=SimpleNamespace(available=True, runtime=runtime, corpus=None))
    row = {'corpus_fingerprint': 'corpus', 'accepted': True, 'status': 'answered',
           'reason': None, 'generation_usage': {}, 'verification_usage': {}}
    evaluate = AsyncMock(side_effect=ValueError('PRIVATE') if failure else None,
                         return_value=row)
    monkeypatch.setattr(bench, 'evaluate_question', evaluate)
    cases = [{'id': f'case-{i}', 'text': 'PRIVATE'} for i in range(8)]
    report = asyncio.run(bench.collect(Settings(tmp_path / 'model', 'a' * 40), cases, output,
                                       service_factory=lambda _: service))
    assert report['status'] == ('failed' if failure else 'completed')
    assert evaluate.await_count == (1 if failure else 8)
    service.close.assert_awaited_once()
    assert 'PRIVATE' not in json.dumps(report)
