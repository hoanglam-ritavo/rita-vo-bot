"""Bounded JSON memory cache, owned by one application and one event loop.

Expiry and LRU metadata belong to the manager, not aiocache's timer callbacks.
Expired entries are reclaimed on the next operation or on close; no background
task, global registry, database connection or network service is created.
"""
import asyncio
from collections import OrderedDict
import json
import hashlib
import math
import time
from aiocache import SimpleMemoryCache
from starlette.requests import Request


def _positive_duration(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError('Cache TTL must be a positive finite number of seconds.')
    return value


def _json_text(value):
    """Reject Python-only values instead of silently coercing them into JSON."""
    pending = [value]
    # json.dumps detects cycles; validate types only after that bounded traversal.
    try:
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False,
                                separators=(',', ':'))
        while pending:
            item = pending.pop()
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    raise ValueError
                pending.extend(item.values())
            elif type(item) is list:
                pending.extend(item)
            elif item is not None and type(item) not in (str, int, float, bool):
                raise ValueError
        return serialized, len(serialized.encode('utf-8'))
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        raise ValueError('Cache values must be valid JSON data.') from None


class CacheManager:
    """Async operations are serialized; multi-operation transactions are not.

    Limits bound entry count and serialized value size, not total process RAM.
    This manager must not be called from another event loop or worker thread.
    """

    def __init__(self, *, default_ttl=300, max_entries=1024, max_value_bytes=64 * 1024):
        self._default_ttl = _positive_duration(default_ttl)
        for limit in (max_entries, max_value_bytes):
            if type(limit) is not int or limit <= 0:
                raise ValueError('Cache limits must be positive integers.')
        self._max_entries = max_entries
        self._max_value_bytes = max_value_bytes
        self._backend = SimpleMemoryCache(timeout=0)
        self._entries = OrderedDict()
        self._lock = asyncio.Lock()
        self._loop = None
        self._closed = False

    def _check_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError('Cache manager belongs to another event loop.')

    def _check_open(self):
        if self._closed:
            raise RuntimeError('Cache manager is closed.')

    @staticmethod
    def _namespace(namespace):
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError('Cache namespace must be a nonempty string.')
        return namespace

    @classmethod
    def _key(cls, key, namespace):
        cls._namespace(namespace)
        if not isinstance(key, str) or not key.strip():
            raise ValueError('Cache key must be a nonempty string.')
        encoded = json.dumps([namespace, key], ensure_ascii=True, separators=(',', ':'))
        # aiocache debug logs include backend keys; keep caller data out of them.
        return hashlib.sha256(encoded.encode('ascii')).hexdigest()

    async def _remove(self, key):
        await self._backend.delete(key)
        del self._entries[key]

    async def _expire(self):
        now = time.monotonic()
        for key, (_, deadline) in list(self._entries.items()):
            if deadline <= now:
                await self._remove(key)

    async def get(self, key, *, namespace, default=None):
        self._check_loop()
        async with self._lock:
            self._check_open()
            encoded = self._key(key, namespace)
            await self._expire()
            if encoded not in self._entries:
                return default
            value = await self._backend.get(encoded)
            self._entries.move_to_end(encoded)
            return json.loads(value)

    async def set(self, key, value, *, namespace, ttl=None):
        self._check_loop()
        async with self._lock:
            self._check_open()
            encoded = self._key(key, namespace)
            duration = self._default_ttl if ttl is None else _positive_duration(ttl)
            serialized, size = _json_text(value)
            if size > self._max_value_bytes:
                raise ValueError('Cache value exceeds the serialized size limit.')
            await self._expire()
            if encoded not in self._entries and len(self._entries) >= self._max_entries:
                await self._remove(next(iter(self._entries)))
            # Disable backend timers: the manager owns expiry and metadata together.
            await self._backend.set(encoded, serialized, ttl=0)
            self._entries[encoded] = (namespace, time.monotonic() + duration)
            self._entries.move_to_end(encoded)

    async def delete(self, key, *, namespace):
        self._check_loop()
        async with self._lock:
            self._check_open()
            encoded = self._key(key, namespace)
            await self._expire()
            if encoded not in self._entries:
                return False
            await self._remove(encoded)
            return True

    async def clear(self, *, namespace):
        self._check_loop()
        async with self._lock:
            self._check_open()
            self._namespace(namespace)
            await self._expire()
            for key, (owner, _) in list(self._entries.items()):
                if owner == namespace:
                    await self._remove(key)

    async def close(self):
        self._check_loop()
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                await self._backend.clear()
            finally:
                self._entries.clear()
                await self._backend.close()


def get_cache_manager(request: Request) -> CacheManager:
    """Resolve the current app's cache; never construct a process-global fallback."""
    manager = getattr(request.app.state, 'cache_manager', None)
    if manager is None:
        raise RuntimeError('Application cache is not initialized.')
    manager._check_open()
    return manager
