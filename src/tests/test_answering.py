"""Synthetic API/model contract checks; no real model or database required."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from src.answering.config import Settings
from src.answering.contracts import AnswerError, ModelAnswer
from src.answering.ollama import OllamaClient
from src.answering.service import AnswerService, build_context, validate_answer
from src.api.app import create_app
from src.database.runtime import run_async
from src.retrieval import RetrievalError


def settings():
    return Settings(Path('unused'), 'a' * 40)


def match(content='Employees may apply subject to eligibility.'):
    return dict(content=content, title='Synthetic policy', chunk_id='chunk', document_id='doc',
                revision_id='rev', source_spans=[], applicability='All employees',
                effective_from=None, effective_to=None)


def output(**changes):
    values = dict(answer='Có thể nộp đơn khi đủ điều kiện [S1].', status='answered',
                  citations=['S1'], support='direct', conflict=False, reason='Nguồn nêu trực tiếp điều kiện.')
    return ModelAnswer(**(values | changes))


def service():
    retrieval = AsyncMock()
    retrieval.retrieve.return_value = {'status': 'matches_found', 'matches': [match()]}
    llm = AsyncMock()
    llm.generate.return_value = output()
    return AnswerService(settings(), retrieval=retrieval, llm=llm,
                         database_check=AsyncMock(), database_close=AsyncMock())


def test_http_success_reuses_service_and_closes():
    svc = service()
    with TestClient(create_app(settings(), service_factory=lambda _: svc)) as client:
        assert client.get('/docs').status_code == 200
        assert client.get('/health/live').status_code == 200
        assert client.get('/health/ready').status_code == 200
        for _ in range(2):
            result = client.post('/api/v1/ask', json={'text': 'Điều kiện là gì?'})
            assert result.status_code == 200
            data = result.json()
            assert data['sources'][0]['chunk_id'] == 'chunk'
            assert data['confidence'] == {'level': 'high', 'reason': output().reason, 'calibrated': False}
            assert data['timings']['total_seconds'] >= 0
    svc.retrieval.start.assert_awaited_once()
    svc.retrieval.close.assert_awaited_once()
    svc.llm.close.assert_awaited_once()
    svc.database_close.assert_awaited_once()


@pytest.mark.parametrize('text', ['', '   ', 12, None, 'x' * 8001])
def test_invalid_input(text):
    svc = service()
    with TestClient(create_app(settings(), service_factory=lambda _: svc)) as client:
        assert client.post('/api/v1/ask', json={'text': text}).status_code == 422
    svc.retrieval.retrieve.assert_not_awaited()


def test_partial_startup_cleanup():
    svc = service()
    svc.llm.start.side_effect = RuntimeError('sensitive detail')
    with pytest.raises(RuntimeError, match='API lifecycle failed'):
        with TestClient(create_app(settings(), service_factory=lambda _: svc)):
            pass
    svc.retrieval.close.assert_awaited_once()
    svc.llm.close.assert_awaited_once()
    svc.database_close.assert_awaited_once()


@pytest.mark.parametrize('status', ['no_matches', 'no_eligible_documents'])
def test_no_evidence_does_not_generate(status):
    async def check():
        svc = service()
        await svc.start()
        svc.retrieval.retrieve.return_value = {'status': status, 'matches': []}
        result = await svc.ask('Question')
        assert result.status == 'insufficient_evidence'
        svc.llm.generate.assert_not_awaited()
    run_async(check())


@pytest.mark.parametrize('change', [
    {'citations': ['S99'], 'answer': 'Invented [S99]'},
    {'citations': [], 'answer': 'Uncited answer'},
    {'citations': ['S1', 'S1']}, {'conflict': True}, {'support': 'none'},
    {'answer': '   '},
])
def test_invalid_citations_and_assessment(change):
    with pytest.raises(AnswerError):
        validate_answer(output(**change), {'S1': match()})


@pytest.mark.parametrize(('support', 'level'), [('direct', 'high'), ('partial', 'medium'), ('indirect', 'low')])
def test_confidence(support, level):
    confidence, _ = validate_answer(output(support=support), {'S1': match()})
    assert confidence.level == level
    assert confidence.calibrated is False


@pytest.mark.parametrize('status', ['needs_clarification', 'unsupported', 'insufficient_evidence'])
def test_unanswered(status):
    confidence, sources = validate_answer(output(status=status, support='none', citations=[],
                                                  answer='Cần bổ sung thông tin.'), {})
    assert confidence.level == 'insufficient' and sources == []


def test_budget_never_splits_chunk():
    messages, selected = build_context('Question', [match(), match('ắ' * 9000)])
    assert len(selected) == 1
    assert 'ắ' not in messages[1]['content']
    with pytest.raises(AnswerError, match='question_exceeds_context_budget'):
        build_context('ắ' * 8000, [])


def test_busy_and_cancellation_release():
    async def check():
        svc = service()
        await svc.start()
        entered = asyncio.Event()
        async def wait(*args):
            entered.set()
            await asyncio.Event().wait()
        svc.retrieval.retrieve.side_effect = wait
        task = asyncio.create_task(svc.ask('First'))
        await entered.wait()
        with pytest.raises(AnswerError) as exc:
            await svc.ask('Second')
        assert exc.value.status_code == 429
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not svc.busy
    run_async(check())


@pytest.mark.parametrize(('failure', 'code'), [(RetrievalError('question_exceeds_model_limit'), 422),
                                              (RetrievalError('database_search_failed'), 503),
                                              (TimeoutError(), 504)])
def test_retrieval_errors(failure, code):
    svc = service()
    svc.retrieval.retrieve.side_effect = failure
    with TestClient(create_app(settings(), service_factory=lambda _: svc)) as client:
        assert client.post('/api/v1/ask', json={'text': 'Question'}).status_code == code


def test_mismatch_is_not_empty_evidence():
    svc = service()
    svc.retrieval.retrieve.return_value = {'status': 'model_mismatch', 'matches': []}
    with TestClient(create_app(settings(), service_factory=lambda _: svc)) as client:
        assert client.post('/api/v1/ask', json={'text': 'Question'}).status_code == 503


@pytest.mark.parametrize('malformed', ['json', 'truncated', 'missing'])
def test_ollama_rejects_bad_output(malformed):
    async def check():
        llm = OllamaClient(settings())
        await llm.client.aclose()
        data = {'done': True, 'done_reason': 'stop', 'message': {'content': output().model_dump_json()}}
        if malformed == 'json':
            data['message']['content'] = 'not json'
        elif malformed == 'truncated':
            data['done_reason'] = 'length'
        else:
            data = {}
        llm.client = httpx.AsyncClient(base_url=settings().ollama_url,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data)))
        try:
            with pytest.raises(AnswerError) as exc:
                await llm.generate([])
            assert exc.value.status_code == 502
        finally:
            await llm.close()
    run_async(check())


def test_local_only_warmup_contract():
    async def check():
        llm = OllamaClient(settings())
        await llm.client.aclose()
        def handle(request):
            if request.url.path == '/api/tags':
                return httpx.Response(200, json={'models': [{'name': 'qwen3:4b', 'digest': 'abc'}]})
            if request.url.path == '/api/show':
                return httpx.Response(200, json={})
            if request.url.path == '/api/version':
                return httpx.Response(200, json={'version': 'test'})
            body = json.loads(request.content)
            assert body['keep_alive'] == -1 and body['think'] is False and body['stream'] is False
            assert body['options']['num_ctx'] == 8192
            return httpx.Response(200, json={'done': True, 'done_reason': 'stop',
                                           'message': {'content': output().model_dump_json()}})
        llm.client = httpx.AsyncClient(base_url=settings().ollama_url, transport=httpx.MockTransport(handle))
        try:
            await llm.start()
            assert llm.digest == 'abc' and llm.version == 'test'
        finally:
            await llm.close()
    run_async(check())


@pytest.mark.parametrize('data', [{'models': []}, {'models': [{'name': 'qwen3:4b', 'digest': 'abc', 'remote_model': 'cloud'}]}])
def test_missing_or_remote_model(data):
    async def check():
        llm = OllamaClient(settings())
        await llm.client.aclose()
        llm.client = httpx.AsyncClient(base_url=settings().ollama_url,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data)))
        try:
            with pytest.raises(AnswerError):
                await llm.check()
        finally:
            await llm.close()
    run_async(check())


@pytest.mark.parametrize(('failure', 'status'), [(httpx.ConnectError('private'), 503),
                                               (httpx.ReadTimeout('private'), 504)])
def test_ollama_transport_failures(failure, status):
    async def check():
        llm = OllamaClient(settings())
        await llm.client.aclose()
        def handle(request):
            raise failure
        llm.client = httpx.AsyncClient(base_url=settings().ollama_url, transport=httpx.MockTransport(handle))
        try:
            with pytest.raises(AnswerError) as exc:
                await llm.check()
            assert exc.value.status_code == status
            assert 'private' not in str(exc.value)
        finally:
            await llm.close()
    run_async(check())


def test_changed_digest_rejected():
    async def check():
        llm = OllamaClient(settings())
        await llm.client.aclose()
        llm.digest = 'original'
        llm.client = httpx.AsyncClient(base_url=settings().ollama_url, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={'models': [{'name': 'qwen3:4b', 'digest': 'changed'}]}
                                          if request.url.path == '/api/tags' else {})))
        try:
            with pytest.raises(AnswerError, match='llm_model_changed'):
                await llm.check()
        finally:
            await llm.close()
    run_async(check())


def test_cleanup_continues_after_close_failure():
    async def check():
        svc = service()
        svc.retrieval.close.side_effect = RuntimeError('failure')
        with pytest.raises(RuntimeError):
            await svc.close()
        svc.llm.close.assert_awaited_once()
        svc.database_close.assert_awaited_once()
    run_async(check())


def test_readiness_failure():
    svc = service()
    with TestClient(create_app(settings(), service_factory=lambda _: svc)) as client:
        svc.database_check.side_effect = RuntimeError('credential')
        response = client.get('/health/ready')
        assert response.status_code == 503
        assert 'credential' not in response.text
        assert client.get('/health/live').status_code == 200


def test_imports_and_app_factory_do_not_initialize_resources():
    import subprocess
    import sys
    code = '''
from unittest.mock import patch
with patch('dotenv.load_dotenv', side_effect=AssertionError('configuration at import')), \\
     patch('socket.socket', side_effect=AssertionError('socket at import')):
    import src.cli.serve_api
    from src.api.app import create_app
    app = create_app()
    assert app.title
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('changes', [{'host': '0.0.0.0'}, {'ollama_url': 'https://example.com'},
                                     {'llm_model': 'qwen3:cloud'}, {'top_k': 0}])
def test_config_rejects_unsafe_or_invalid_values(changes):
    with pytest.raises(ValueError):
        Settings(**({'model_path': Path('unused'), 'model_revision': 'a' * 40} | changes))


def test_uvicorn_loopback_on_repository_event_loop():
    import socket
    import uvicorn

    async def check():
        svc = service()
        app = create_app(settings(), service_factory=lambda _: svc)
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='critical'))
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        pytest.fail('Server did not start')
                    await asyncio.sleep(0.01)
                async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', trust_env=False) as client:
                    assert (await client.get('/docs')).status_code == 200
                    result = await client.post('/api/v1/ask', json={'text': 'Điều kiện là gì?'})
                    assert result.status_code == 200
                    assert result.json()['sources'][0]['id'] == 'S1'
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
            sock.close()
        svc.retrieval.start.assert_awaited_once()
        svc.database_close.assert_awaited_once()
    run_async(check())
