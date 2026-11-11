from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.database.config import ConfigurationError, database_url
from app.database.migrations import migration_options
from app.database.runtime import run_async
from app.database import models  # noqa: F401 -- register application metadata


def run_sync_migrations(connection):
    context.configure(connection=connection, **migration_options())
    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    engine = create_async_engine(
        database_url(migration=True), poolclass=pool.NullPool,
        hide_parameters=True, connect_args={'connect_timeout': 10},
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_sync_migrations)
    finally:
        await engine.dispose()


try:
    if context.is_offline_mode():
        context.configure(
            url=database_url(migration=True), literal_binds=True,
            dialect_opts={'paramstyle': 'named'}, **migration_options(),
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        run_async(run_online())
except ConfigurationError as exc:
    raise SystemExit(str(exc)) from None
except Exception:
    raise SystemExit(
        'Migration failed. Check connectivity, TLS, migration permissions and '
        'server pgvector installation. Ask the DBA to enable vector if necessary.'
    ) from None
