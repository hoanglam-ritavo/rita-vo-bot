import asyncio
import sys
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar('T')


def run_async(coroutine: Coroutine[Any, Any, T]) -> T:
    """Use a psycopg-compatible loop on Windows (Python >= 3.12)."""
    if sys.platform == 'win32':
        return asyncio.run(coroutine, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coroutine)
