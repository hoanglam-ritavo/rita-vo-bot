"""Shared HTTP credential boundary, independent of feature/source eligibility."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from fastapi import FastAPI
import pytest

from src.api.app import create_app
from src.api.config import Settings, load_settings
from src.tests.http_fixtures import SERVICE_KEY, SERVICE_HEADERS, http_service_environment


def owner():
    return SimpleNamespace(settings=Settings(Path('unused'), 'a' * 40, service_key=SERVICE_KEY),
        start=AsyncMock(), close=AsyncMock(), execute=AsyncMock(), ready=AsyncMock(),
        answer_readiness=AsyncMock(return_value=None))


@pytest.mark.parametrize('path', [
    '/api/v1/retrieve', '/api/v1/answer',
    '/api/v1/conversations/22222222-2222-4222-8222-222222222222/messages',
    '/api/v1/conversations/22222222-2222-4222-8222-222222222222/turns/33333333-3333-4333-8333-333333333333',
    '/api/v1/conversations/22222222-2222-4222-8222-222222222222/turns/33333333-3333-4333-8333-333333333333/retry',
    '/api/v1/conversations/22222222-2222-4222-8222-222222222222/purge',
    '/api/v1/future-route', '/health/ready', '/health/answer',
])
@pytest.mark.parametrize('headers', [{}, {'X-Rita-Service-Key': 'wrong'},
    [('X-Rita-Service-Key', SERVICE_KEY), ('X-Rita-Service-Key', SERVICE_KEY)]])
def test_missing_wrong_or_duplicate_key_is_rejected_before_processing(path, headers, caplog):
    current = owner()
    with TestClient(create_app(current.settings, service_factory=lambda _: current)) as client:
        response = client.request('POST' if path.startswith('/api/') else 'GET', path,
            headers=headers, content=b'{malformed-json-private-marker')
        assert response.status_code == 401
        assert response.headers['cache-control'] == 'no-store'
        assert response.json()['error']['code'] == 'service_authentication_required'
    current.execute.assert_not_awaited()
    current.ready.assert_not_awaited()
    current.answer_readiness.assert_not_awaited()
    assert SERVICE_KEY not in response.text + caplog.text
    assert 'malformed-json-private-marker' not in response.text + caplog.text


def test_liveness_is_public_readiness_is_authenticated_and_features_still_apply():
    current = owner()
    with TestClient(create_app(current.settings, service_factory=lambda _: current)) as client:
        assert client.get('/health/live').json() == {'status': 'alive'}
        assert client.get('/health/ready', headers=SERVICE_HEADERS).status_code == 200
        assert client.get('/health/answer', headers=SERVICE_HEADERS).status_code == 200
        # Authentication succeeds, then the actual input/feature guards run.
        assert client.post('/api/v1/retrieve', json={'extra': 'bad'}, headers=SERVICE_HEADERS).status_code == 422
        assert client.get('/api/v1/conversations/22222222-2222-4222-8222-222222222222/messages',
            params={'user_id': '11111111-1111-4111-8111-111111111111'}, headers=SERVICE_HEADERS).status_code == 503
    current.ready.assert_awaited_once()
    current.answer_readiness.assert_awaited_once()
    current.execute.assert_not_awaited()


@pytest.mark.parametrize('key', [None, '', ' '])
def test_http_startup_requires_shared_key_without_type_b_fallback(key, monkeypatch, tmp_path):
    monkeypatch.setattr('src.api.config.REPOSITORY_ROOT', tmp_path)
    monkeypatch.delenv('CHAT_BOT_SERVICE_KEY', raising=False)
    monkeypatch.setenv('TYPE_B_SERVICE_KEY', 'retired-fictional-key')
    if key is not None:
        monkeypatch.setenv('CHAT_BOT_SERVICE_KEY', key)
    with pytest.raises(ValueError, match='CHAT_BOT_SERVICE_KEY'):
        load_settings()


def test_shared_key_preserves_bytes_and_is_absent_from_repr():
    key = ' fictional-service-key-with-whitespace '
    settings = Settings(Path('unused'), 'a' * 40, service_key=key)
    assert settings.service_key == key and key not in repr(settings)


def test_mount_prefix_cannot_bypass_business_authentication():
    current = owner()
    child = create_app(current.settings, service_factory=lambda _: current)
    child.state.retrieval_service = current
    parent = FastAPI()
    parent.mount('/bot', child)
    with TestClient(parent) as client:
        assert client.post('/bot/api/v1/retrieve', json={}).status_code == 401
        assert client.post('/bot/api/v1/retrieve', json={}, headers=SERVICE_HEADERS).status_code == 422
    current.execute.assert_not_awaited()
