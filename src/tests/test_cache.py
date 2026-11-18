"""Application memory cache contracts, with synthetic data and a controlled clock."""
import asyncio
import logging
from types import SimpleNamespace

import pytest

from src.helper import cache as module
from src.helper.cache import CacheManager, get_cache_manager


def test_values_defaults_namespace_and_copy_isolation():
    async def scenario():
        cache = CacheManager()
        marker = object()
        assert await cache.get('missing', namespace='a', default=marker) is marker
        await cache.set('null', None, namespace='a')
        assert await cache.get('null', namespace='a', default=marker) is None
        original = {'items': [1, True, 'fictional', None]}
        await cache.set('key', original, namespace='a')
        original['items'].append('outside')
        result = await cache.get('key', namespace='a')
        assert result == {'items': [1, True, 'fictional', None]}
        result['items'].clear()
        assert (await cache.get('key', namespace='a'))['items']
        await cache.set('key', 2, namespace='a')
        await cache.set('key', 3, namespace='b')
        await cache.set('b:c', 4, namespace='a')
        await cache.set('c', 5, namespace='a:b')
        assert await cache.get('b:c', namespace='a') == 4
        assert await cache.get('c', namespace='a:b') == 5
        assert await cache.delete('key', namespace='a') is True
        assert await cache.delete('key', namespace='a') is False
        await cache.clear(namespace='a')
        assert await cache.get('b:c', namespace='a') is None
        assert await cache.get('key', namespace='b') == 3
        assert await cache.get('c', namespace='a:b') == 5
        await cache.close()
    asyncio.run(scenario())


def test_expiry_overwrite_and_no_read_extension(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0]))

    async def scenario():
        cache = CacheManager()
        await cache.set('default', 1, namespace='test')
        await cache.set('short', 2, namespace='test', ttl=2.5)
        now[0] = 102
        assert await cache.get('short', namespace='test') == 2
        now[0] = 102.5
        assert await cache.get('short', namespace='test') is None
        assert await cache.delete('short', namespace='test') is False
        now[0] = 399
        assert await cache.get('default', namespace='test') == 1
        now[0] = 400
        assert await cache.get('default', namespace='test') is None
        assert not cache._entries and not cache._backend._cache
        await cache.set('overwrite', 1, namespace='test', ttl=1)
        now[0] = 400.5
        await cache.set('overwrite', 2, namespace='test', ttl=2)
        now[0] = 401
        assert await cache.get('overwrite', namespace='test') == 2
        now[0] = 402.5
        assert await cache.get('overwrite', namespace='test') is None
        await cache.close()
    asyncio.run(scenario())


def test_capacity_lru_and_expired_first(monkeypatch):
    now = [0]
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0]))

    async def scenario():
        cache = CacheManager(max_entries=2)
        await cache.set('a', 1, namespace='x')
        await cache.set('b', 2, namespace='y')
        await cache.get('a', namespace='x')
        await cache.set('c', 3, namespace='x')
        assert await cache.get('b', namespace='y') is None
        assert await cache.get('a', namespace='x') == 1
        await cache.set('a', 4, namespace='x', ttl=1)
        now[0] = 1
        await cache.set('d', 5, namespace='x')
        assert await cache.get('c', namespace='x') == 3
        assert len(cache._entries) == len(cache._backend._cache) == 2
        now[0] = 1000
        await cache.clear(namespace='unrelated')
        assert not cache._entries and not cache._backend._cache
        await cache.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('value', [object(), (1, 2), {1: 'value'}, float('nan'),
                                  {'nested': float('inf')}, {'nested': {1, 2}}, '\ud800'])
def test_invalid_json_rejected_without_eviction(value):
    async def scenario():
        cache = CacheManager(max_entries=1)
        await cache.set('keep', 1, namespace='n')
        with pytest.raises(ValueError, match='valid JSON'):
            await cache.set('bad', value, namespace='n')
        assert await cache.get('keep', namespace='n') == 1
        await cache.close()
    asyncio.run(scenario())


def test_cycles_and_utf8_size_limit():
    async def scenario():
        cache = CacheManager(max_entries=1, max_value_bytes=6)
        await cache.set('key', 'éé', namespace='n')  # Four UTF-8 bytes plus quotes.
        with pytest.raises(ValueError, match='size limit'):
            await cache.set('key', 'ééé', namespace='n')
        assert await cache.get('key', namespace='n') == 'éé'
        cycle = []
        cycle.append(cycle)
        with pytest.raises(ValueError, match='valid JSON'):
            await cache.set('cycle', cycle, namespace='n')
        await cache.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('value', [0, -1, True, '5', float('inf'), float('nan')])
def test_invalid_ttl(value):
    with pytest.raises(ValueError):
        CacheManager(default_ttl=value)

    async def scenario():
        cache = CacheManager()
        with pytest.raises(ValueError):
            await cache.set('key', 1, namespace='n', ttl=value)
        await cache.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('name', ['max_entries', 'max_value_bytes'])
@pytest.mark.parametrize('value', [0, -1, True, 1.5, '2'])
def test_invalid_limits(name, value):
    with pytest.raises(ValueError):
        CacheManager(**{name: value})


@pytest.mark.parametrize('key,namespace', [('', 'n'), (' ', 'n'), (1, 'n'),
                                         ('key', ''), ('key', ' '), ('key', None)])
def test_invalid_keys_and_namespaces(key, namespace):
    async def scenario():
        cache = CacheManager()
        for operation in (cache.get, cache.delete):
            with pytest.raises(ValueError):
                await operation(key, namespace=namespace)
        with pytest.raises(ValueError):
            await cache.set(key, 1, namespace=namespace)
        if not isinstance(namespace, str) or not namespace.strip():
            with pytest.raises(ValueError):
                await cache.clear(namespace=namespace)
        await cache.close()
    asyncio.run(scenario())


def test_concurrent_operations_close_and_loop_ownership():
    cache = CacheManager(max_entries=8)

    async def scenario():
        async def write(index):
            await cache.set(str(index), {'index': index}, namespace='n')
            result = await cache.get(str(index), namespace='n')
            assert result is None or result == {'index': index}
        await asyncio.gather(*(write(i) for i in range(100)))
        assert len(cache._entries) == len(cache._backend._cache) == 8
        await cache.close()
        await cache.close()
        assert not cache._entries and not cache._backend._cache
        for operation in (cache.get('x', namespace='n'), cache.set('x', 1, namespace='n'),
                          cache.delete('x', namespace='n'), cache.clear(namespace='n')):
            with pytest.raises(RuntimeError, match='closed'):
                await operation
    asyncio.run(scenario())
    with pytest.raises(RuntimeError, match='another event loop'):
        asyncio.run(cache.get('x', namespace='n'))


def test_accessor_requires_initialized_app():
    with pytest.raises(RuntimeError, match='not initialized'):
        get_cache_manager(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))


def test_debug_logs_do_not_contain_caller_data(caplog):
    async def scenario():
        cache = CacheManager()
        await cache.set('fictional-private-key', {'text': 'fictional-private-value'},
                        namespace='fictional-private-namespace')
        await cache.get('fictional-private-key', namespace='fictional-private-namespace')
        await cache.delete('fictional-private-key', namespace='fictional-private-namespace')
        await cache.clear(namespace='fictional-private-namespace')
        await cache.close()
    with caplog.at_level(logging.DEBUG, logger='aiocache'):
        asyncio.run(scenario())
    assert 'fictional-private' not in caplog.text
