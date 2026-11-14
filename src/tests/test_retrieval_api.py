"""Synthetic HTTP lifecycle and retrieval contracts; no database/model needed."""
import asyncio
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.config import Settings, load_settings
from src.api.contracts import ApiError
from src.api.service import ApiService
from src.database.runtime import run_async
from src.retrieval import RetrievalService, RetrievalError
from src.ingestion import embeddings
from src.retrieval import storage


def settings(**changes):
    return Settings(**(dict(model_path=Path('unused'), model_revision='a' * 40) | changes))


def match():
    return dict(chunk_id='chunk', chunk_index=0, run_id='run', document_id='doc', revision_id='rev',
        content='Synthetic condition and exception.', cosine_distance=0.1, title='Policy',
        author_id=None, role=None, source_path='policy.txt', version_label=None,
        effective_from=None, effective_to=None, applicability='All staff',
        source_spans=[{'segment_id': 's1', 'start': 0, 'end': 10, 'location': {'kind': 'text', 'line_start': 1, 'line_end': 1}}])


def service(**changes):
    retrieval = AsyncMock()
    retrieval.started = True
    retrieval.retrieve.return_value = {'status': 'matches_found', 'matches': [match()], 'fallback_reason': None}
    return ApiService(settings(**changes), retrieval=retrieval,
                      database_check=AsyncMock(), database_close=AsyncMock())


def test_http_contract_lifecycle_defaults_and_removed_route():
    svc = service(top_k=7)
    with TestClient(create_app(svc.settings, service_factory=lambda _: svc)) as client:
        assert client.get('/health/live').status_code == client.get('/health/ready').status_code == 200
        assert client.post('/api/v1/ask', json={'text': 'Question'}).status_code == 404
        schema = client.get('/openapi.json').json()
        assert '/api/v1/ask' not in schema['paths']
        assert 'rerank_score' in schema['components']['schemas']['Match']['properties']
        assert 'relevance' in schema['components']['schemas']['RetrievalResponse']['properties']
        result = client.post('/api/v1/retrieve', json={'text': '  Question  '})
        assert result.status_code == 200
        data = result.json()
        assert set(data) == {'status', 'matches', 'timings', 'relevance', 'fallback_reason'}
        assert data['matches'] == [match() | {'rerank_score': None}]
        assert data['relevance'] == {'status': 'unassessed', 'warnings': ['evidence_completeness_not_assessed']}
        assert set(data['timings']) == {'retrieval_seconds', 'total_seconds', 'rerank_seconds'}
        svc.retrieval.retrieve.assert_awaited_with('Question', 7)
        assert client.post('/api/v1/retrieve', json={'text': 'Second', 'top_k': 20}).status_code == 200
        svc.retrieval.retrieve.assert_awaited_with('Second', 20)
        svc.retrieval.start.assert_awaited_once()
    svc.retrieval.close.assert_awaited_once()
    svc.database_close.assert_awaited_once()


@pytest.mark.parametrize('body', [{}, {'text': ''}, {'text': '   '}, {'text': 1}, {'text': None},
    {'text': 'x' * 8001}, {'text': 'ok', 'extra': 'secret'},
    *[{'text': 'ok', 'top_k': value} for value in (0, 21, True, '5', 1.5, None)]])
def test_invalid_requests(body, caplog):
    svc = service()
    with TestClient(create_app(svc.settings, service_factory=lambda _: svc)) as client:
        response = client.post('/api/v1/retrieve', json=body)
        assert response.status_code == 422
        assert response.json()['error']['code'] == 'invalid_request'
        assert 'secret' not in response.text + caplog.text
    svc.retrieval.retrieve.assert_not_awaited()


@pytest.mark.parametrize('status', ['no_eligible_documents', 'no_matches', 'model_mismatch'])
def test_empty_and_mismatch(status):
    svc = service()
    svc.retrieval.retrieve.return_value = {'status': status, 'matches': [], 'fallback_reason': 'no_candidates' if status == 'no_matches' else 'no_eligible_documents'}
    with TestClient(create_app(svc.settings, service_factory=lambda _: svc)) as client:
        response = client.post('/api/v1/retrieve', json={'text': 'Question'})
    assert response.status_code == (503 if status == 'model_mismatch' else 200)
    assert response.json().get('status', response.json().get('error', {}).get('code')) == status


@pytest.mark.parametrize('error,status,code', [
    (RetrievalError('question_exceeds_model_limit'), 422, 'question_exceeds_model_limit'),
    (RetrievalError('database_search_failed'), 503, 'database_search_failed'),
    (RetrievalError('query_embedding_failed'), 503, 'query_embedding_failed'),
    (TimeoutError(), 504, 'retrieval_timeout'),
    (RuntimeError('secret credentials'), 503, 'retrieval_failed')])
def test_safe_failures(error, status, code, caplog):
    svc = service()
    svc.retrieval.retrieve.side_effect = error
    with TestClient(create_app(svc.settings, service_factory=lambda _: svc)) as client:
        response = client.post('/api/v1/retrieve', json={'text': 'private question'})
    assert response.status_code == status and response.json()['error']['code'] == code
    assert 'secret' not in response.text + caplog.text
    assert 'private question' not in caplog.text


@pytest.mark.parametrize('stage', ['model', 'database'])
def test_partial_startup_cleanup(stage):
    svc = service()
    target = svc.retrieval.start if stage == 'model' else svc.database_check
    target.side_effect = RuntimeError('private diagnostic')
    with pytest.raises(RuntimeError, match='API lifecycle failed'):
        with TestClient(create_app(svc.settings, service_factory=lambda _: svc)):
            pass
    svc.retrieval.close.assert_awaited_once()
    svc.database_close.assert_awaited_once()


def test_readiness_and_shutdown_failure():
    async def scenario():
        svc = service()
        with pytest.raises(ApiError, match='not_ready'):
            await svc.retrieve('Question')
        await svc.start()
        await svc.start()
        svc.retrieval.start.assert_awaited_once()
        svc.retrieval.started = False
        with pytest.raises(ApiError, match='not_ready'):
            await svc.ready()
        svc.retrieval.started = True
        svc.database_check.side_effect = RuntimeError('private')
        with pytest.raises(ApiError, match='not_ready'):
            await svc.ready()
        svc.retrieval.close.side_effect = RuntimeError('close failed')
        with pytest.raises(RuntimeError):
            await svc.close()
        svc.database_close.assert_awaited_once()
    run_async(scenario())


@pytest.mark.parametrize('cancel', [True, False])
def test_worker_drains_before_releasing_busy_and_health_stays_live(monkeypatch, cancel):
    entered, release = threading.Event(), threading.Event()
    def encode(question):
        entered.set()
        assert release.wait(10)
        return [1.0] + [0.0] * 383
    monkeypatch.setattr(embeddings, 'model_fingerprint', Mock(return_value='fingerprint'))
    monkeypatch.setattr(embeddings, 'load_model', Mock(return_value=object()))
    monkeypatch.setattr(storage, 'search', AsyncMock(return_value={'status': 'no_matches', 'matches': []}))
    async def scenario():
        svc = ApiService(settings(), database_check=AsyncMock(), database_close=AsyncMock())
        monkeypatch.setattr(svc.retrieval, '_encode', encode)
        await svc.start()
        embeddings.load_model.assert_called_once_with(Path('unused'), device='cpu')
        if not cancel:
            monkeypatch.setattr('src.api.service.REQUEST_TIMEOUT_SECONDS', 0.02)
        task = asyncio.create_task(svc.retrieve('Question'))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            if cancel:
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()  # A second cancellation must also keep the worker alive.
            else:
                await asyncio.sleep(0.05)
            assert not task.done() and svc.busy
            app = create_app(svc.settings)
            app.state.retrieval_service = svc
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                assert (await client.get('/health/live')).status_code == 200
                assert (await client.get('/health/ready')).status_code == 200
                assert (await client.post('/api/v1/retrieve', json={'text': 'Second'})).status_code == 429
            closing = asyncio.create_task(svc.close())
            await asyncio.sleep(0)
            assert not closing.done()
        finally:
            release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ApiError, match='retrieval_timeout') as exc:
                await task
            assert exc.value.status_code == 504
        await closing
        assert not svc.busy and not svc.retrieval.started
        svc.database_close.assert_awaited_once()
        storage.search.assert_not_awaited()
    run_async(scenario())


@pytest.mark.parametrize('changes', [{'host': '0.0.0.0'}, {'port': 0}, {'port': True},
    {'top_k': 0}, {'top_k': True}, {'model_revision': 'short'}])
def test_configuration_validation(changes):
    with pytest.raises(ValueError):
        settings(**changes)


def test_configuration_precedence(monkeypatch):
    monkeypatch.setattr('src.api.config.load_dotenv', lambda *a, **kw: None)
    monkeypatch.setenv('E5_MODEL_PATH', 'relative/model')
    monkeypatch.setenv('RETRIEVAL_TOP_K', '9')
    monkeypatch.setenv('ANSWER_TOP_K', '99')
    monkeypatch.setenv('OLLAMA_MODEL', 'unused')
    result = load_settings()
    assert result.top_k == 9 and result.model_path.is_absolute()
    assert not hasattr(result, 'llm_model')


def test_default_model_uses_durable_directory_from_any_working_directory(monkeypatch, tmp_path):
    from src.paths import REPOSITORY_ROOT

    monkeypatch.setattr('src.api.config.load_dotenv', lambda *a, **kw: None)
    monkeypatch.delenv('E5_MODEL_PATH', raising=False)
    monkeypatch.setenv('E5_MODEL_REVISION', 'a' * 40)
    monkeypatch.chdir(tmp_path)
    result = load_settings()
    assert result.model_path == REPOSITORY_ROOT / 'models/embeddings/multilingual-e5-small' / ('a' * 40)


def test_environment_load_preserves_explicit_model_and_artifact_paths(monkeypatch, tmp_path):
    from src.paths import REPOSITORY_ROOT

    local_env = tmp_path / '.env'
    local_env.write_text('E5_MODEL_PATH=unused/from-file\nINTENT_MODE=off\n', encoding='utf-8')
    from dotenv import load_dotenv
    monkeypatch.setattr('src.api.config.load_dotenv', lambda *a, **kw: load_dotenv(local_env, **kw))
    monkeypatch.setenv('E5_MODEL_PATH', str(tmp_path / 'provisioned-model'))
    monkeypatch.setenv('RERANK_PROFILE_PATH', 'artifacts/rerank/release-1/relevance-profile.json')
    monkeypatch.setenv('INTENT_MODE', 'off')
    result = load_settings()
    assert result.model_path == tmp_path / 'provisioned-model'
    assert result.rerank.profile_path == REPOSITORY_ROOT / 'artifacts/rerank/release-1/relevance-profile.json'
    assert result.intent.mode == 'off'


def test_import_has_no_model_database_or_environment_side_effects():
    script = '''
import sys
import src.api.app, src.cli.serve_api
assert not any(m in sys.modules for m in ('src.database', 'sentence_transformers', 'sqlalchemy'))
'''
    result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_accepted_relevance_preserves_provenance_and_reports_raw_score():
    svc = service()
    source = match() | {'rerank_score': -4.5}
    svc.retrieval.retrieve.return_value = dict(status='matches_found', matches=[source], fallback_reason=None,
        relevance={'status': 'relevant_candidates', 'warnings': ['evidence_completeness_not_assessed']},
        rerank_seconds=.25)
    with TestClient(create_app(svc.settings, service_factory=lambda _: svc)) as client:
        response = client.post('/api/v1/retrieve', json={'text': 'A synthetic unrelated question'})
        assert response.status_code == 200
        body = response.json()
        assert body['matches'] == [source]
        assert body['relevance']['status'] == 'relevant_candidates'
        assert body['timings']['rerank_seconds'] == .25
        svc.retrieval.retrieve.side_effect = RetrievalError('reranking_failed', 2)
        failure = client.post('/api/v1/retrieve', json={'text': 'Another synthetic question'})
        assert failure.status_code == 503
        assert failure.json()['error']['code'] == 'reranking_failed'


@pytest.mark.parametrize('reason', ['below_relevance_threshold', 'relevance_unavailable', 'no_candidates'])
def test_fallback_http_contract(reason):
    svc = service()
    svc.retrieval.retrieve.return_value = dict(status='no_matches', matches=[], fallback_reason=reason)
    with TestClient(create_app(svc.settings, service_factory=lambda _: svc)) as client:
        schema = client.get('/openapi.json').json()['components']['schemas']['RetrievalResponse']
        assert 'fallback_reason' in schema['required']
        response = client.post('/api/v1/retrieve', json={'text': 'Question', 'top_k': 5})
        assert response.status_code == 200
        assert response.json()['matches'] == []
        assert response.json()['fallback_reason'] == reason


def test_inconsistent_service_response_is_not_empty_evidence():
    svc = service()
    svc.retrieval.retrieve.return_value['fallback_reason'] = 'relevance_unavailable'
    with TestClient(create_app(svc.settings, service_factory=lambda _: svc)) as client:
        assert client.post('/api/v1/retrieve', json={'text': 'Question'}).status_code == 503


@pytest.mark.parametrize('raw,expected', [(None, True), ('true', True), ('TRUE', True),
                                        ('false', False), ('FaLsE', False)])
def test_relevance_filter_environment(monkeypatch, raw, expected):
    monkeypatch.setattr('src.api.config.load_dotenv', lambda *a, **kw: None)
    monkeypatch.delenv('RELEVANCE_FILTER_ENABLED', raising=False)
    if raw is not None:
        monkeypatch.setenv('RELEVANCE_FILTER_ENABLED', raw)
    assert load_settings().relevance_filter_enabled is expected


@pytest.mark.parametrize('raw', ['', 'yes', '0', ' true '])
def test_relevance_filter_rejects_invalid_environment(monkeypatch, raw):
    monkeypatch.setattr('src.api.config.load_dotenv', lambda *a, **kw: None)
    monkeypatch.setenv('RELEVANCE_FILTER_ENABLED', raw)
    with pytest.raises(ValueError, match='Invalid relevance filter configuration'):
        load_settings()


def test_relevance_filter_process_environment_overrides_file(monkeypatch, tmp_path):
    from dotenv import load_dotenv
    env_file = tmp_path / '.env'
    env_file.write_text('RELEVANCE_FILTER_ENABLED=true\n', encoding='utf-8')
    monkeypatch.setattr('src.api.config.load_dotenv', lambda *a, **kw: load_dotenv(env_file, **kw))
    monkeypatch.setenv('RELEVANCE_FILTER_ENABLED', 'false')
    assert load_settings().relevance_filter_enabled is False
    monkeypatch.delenv('RELEVANCE_FILTER_ENABLED')
    assert load_settings().relevance_filter_enabled is True


@pytest.mark.parametrize('value', ['false', 0, None])
def test_api_settings_require_boolean_filter_setting(value):
    with pytest.raises(ValueError, match='Invalid relevance filter configuration'):
        settings(relevance_filter_enabled=value)


@pytest.mark.parametrize('filter_enabled', [True, False])
@pytest.mark.parametrize('rerank_enabled', [True, False])
def test_http_independent_filter_and_rerank_with_real_service(monkeypatch, filter_enabled, rerank_enabled):
    from src.retrieval.reranking import OnnxReranker, RerankSettings
    monkeypatch.setattr(embeddings, 'model_fingerprint', lambda *_: 'synthetic')
    monkeypatch.setattr(embeddings, 'load_model', lambda *a, **kw: object())
    monkeypatch.setattr(RetrievalService, '_encode', lambda *a: [1.] + [0.] * 383)
    monkeypatch.setattr(OnnxReranker, 'start', lambda self: setattr(self, 'fingerprint', 'synthetic'))
    monkeypatch.setattr(OnnxReranker, 'score', lambda *a: ([1., 9., 4.], 3))
    def search(*a, **kw):
        return dict(status='matches_found', corpus_fingerprint='corpus', matches=[
            dict(match(), chunk_id=f'chunk-{i}', chunk_index=i, cosine_distance=i / 10) for i in range(3)])
    monkeypatch.setattr(storage, 'search', AsyncMock(side_effect=search))
    svc = ApiService(settings(relevance_filter_enabled=filter_enabled,
        rerank=RerankSettings(enabled=rerank_enabled)), database_check=AsyncMock(), database_close=AsyncMock())
    assert svc.retrieval.relevance_filter_enabled is filter_enabled
    with TestClient(create_app(svc.settings, service_factory=lambda _: svc)) as client:
        if filter_enabled and rerank_enabled:
            svc.retrieval._reranker.profile = dict(fingerprint='synthetic', corpus_fingerprint='corpus',
                                                   threshold=8., holdout_passed=True)
        response = client.post('/api/v1/retrieve', json=dict(text='Fictional policy?', top_k=2))
        assert response.status_code == 200
        result = response.json()
        expected = (['chunk-1'] if rerank_enabled else []) if filter_enabled else (
            ['chunk-1', 'chunk-2'] if rerank_enabled else ['chunk-0', 'chunk-1'])
        assert [m['chunk_id'] for m in result['matches']] == expected
        assert result['fallback_reason'] == (None if expected else 'relevance_unavailable')
        assert result['status'] == ('matches_found' if expected else 'no_matches')
        assert 'candidate_order' not in result and 'reranker_fingerprint' not in result
        if not filter_enabled:
            assert result['relevance'] == dict(status='unassessed', warnings=[
                'evidence_completeness_not_assessed', 'relevance_filter_disabled'])
        if not rerank_enabled:
            assert result['timings']['rerank_seconds'] == 0.
            assert all(m['rerank_score'] is None for m in result['matches'])
