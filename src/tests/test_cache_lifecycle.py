"""Cache ownership and cleanup without database/model initialization."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.helper.cache import get_cache_manager


def make_app():
    service = SimpleNamespace(start=AsyncMock(), close=AsyncMock())
    return create_app(object(), service_factory=lambda _: service), service


def test_same_app_dependency_and_new_lifespan_isolation():
    app, service = make_app()
    managers = []

    @app.get('/test-cache')
    async def probe(cache=Depends(get_cache_manager)):
        assert cache is app.state.cache_manager
        managers.append(cache)
        previous = await cache.get('key', namespace='synthetic')
        await cache.set('key', 1, namespace='synthetic')
        return {'previous': previous}

    assert not hasattr(app.state, 'cache_manager')
    with TestClient(app) as client:
        assert client.get('/test-cache').json() == {'previous': None}
        assert client.get('/test-cache').json() == {'previous': 1}
    assert managers[0] is managers[1]
    assert managers[0]._closed
    assert not hasattr(app.state, 'cache_manager')
    with TestClient(app) as client:
        assert client.get('/test-cache').json() == {'previous': None}
    assert managers[2] is not managers[0]
    assert service.close.await_count == 2


def test_different_apps_are_isolated():
    async def scenario():
        first, _ = make_app()
        second, _ = make_app()
        async with first.router.lifespan_context(first), second.router.lifespan_context(second):
            a, b = first.state.cache_manager, second.state.cache_manager
            assert a is not b
            await a.set('key', 1, namespace='n')
            assert await b.get('key', namespace='n') is None
        assert a._closed and b._closed
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['factory', 'start', 'close'])
def test_cleanup_on_lifecycle_failure(failure):
    async def scenario():
        service = SimpleNamespace(start=AsyncMock(), close=AsyncMock())
        captured = []

        def factory(_):
            captured.append(app.state.cache_manager)
            if failure == 'factory':
                raise ValueError('synthetic factory failure')
            return service

        if failure in ('start', 'close'):
            getattr(service, failure).side_effect = ValueError('synthetic failure')
        app = create_app(object(), service_factory=factory)
        with pytest.raises(RuntimeError, match='API (lifecycle|shutdown) failed'):
            async with app.router.lifespan_context(app):
                await app.state.cache_manager.set('key', 1, namespace='n')
        assert captured[0]._closed
        assert not captured[0]._entries and not captured[0]._backend._cache
        assert not hasattr(app.state, 'cache_manager')
        if failure != 'factory':
            service.close.assert_awaited_once()
    asyncio.run(scenario())
