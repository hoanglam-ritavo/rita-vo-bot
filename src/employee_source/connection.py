"""Private, independent readonly transaction lifecycle. No ORM or migration registration."""
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .config import EmployeeSourceSettings
from .errors import EmployeeSourceError


class ReadOnlySource:
    def __init__(self, settings: EmployeeSourceSettings | None = None):
        self._settings = settings
        self._engine = None

    def _configure(self):
        if self._settings is None:
            self._settings = EmployeeSourceSettings.from_environment()
        return self._settings

    def _get_engine(self):
        if self._engine is None:
            settings = self._configure()
            self._engine = create_async_engine(
                settings.url, pool_pre_ping=True, pool_size=2, max_overflow=0,
                pool_timeout=10, echo=False, hide_parameters=True,
                isolation_level='REPEATABLE READ',
                connect_args={'connect_timeout': 10, 'options':
                              '-c default_transaction_read_only=on -c statement_timeout=10000'},
            )
        return self._engine

    @asynccontextmanager
    async def _read_scope(self):
        # No connection/session escapes through the public service API.
        async with self._get_engine().connect() as connection:
            async with connection.begin():
                await connection.execute(text('SET TRANSACTION READ ONLY'))
                await connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                if await connection.scalar(text('SHOW transaction_read_only')) != 'on':
                    raise EmployeeSourceError('readonly_required')
                yield connection

    async def close(self):
        engine, self._engine = self._engine, None
        if engine is not None:
            await engine.dispose()
