from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import database_url

_engine: AsyncEngine | None = None
_sessions: async_sessionmaker[AsyncSession] | None = None


class DatabaseCheckError(RuntimeError):
    pass


def get_engine() -> AsyncEngine:
    global _engine, _sessions
    if _engine is None:
        _engine = create_async_engine(
            database_url(), pool_pre_ping=True, echo=False, hide_parameters=True,
            connect_args={'connect_timeout': 10},
        )
        _sessions = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """One transaction/session per task; commit or rollback and always close."""
    get_engine()
    assert _sessions is not None
    async with _sessions.begin() as session:
        yield session


async def check_database() -> str:
    async with get_engine().connect() as connection:
        await connection.execute(text('SELECT 1'))
        version = await connection.scalar(text(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ))
        if version is None:
            raise DatabaseCheckError(
                'pgvector is not enabled. Run alembic upgrade head or contact the DBA.'
            )
        return str(version)


async def close_database() -> None:
    global _engine, _sessions
    engine, _engine, _sessions = _engine, None, None
    if engine is not None:
        await engine.dispose()
