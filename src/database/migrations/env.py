from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from src.database.config import ConfigurationError, database_url
from src.database.migrations import migration_options
from src.database.migrations.errors import Stage, format_error
from src.database.runtime import run_async
from src.database import models  # noqa: F401 -- register application metadata

stage = Stage.CONFIGURATION


def run_sync_migrations(connection):
    context.configure(connection=connection, **migration_options())
    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    global stage
    url = database_url(migration=True)
    stage = Stage.ENGINE
    engine = create_async_engine(
        url, poolclass=pool.NullPool,
        hide_parameters=True, connect_args={'connect_timeout': 10},
    )
    failed = False
    try:
        stage = Stage.CONNECTION
        async with engine.connect() as connection:
            stage = Stage.EXECUTION
            await connection.run_sync(run_sync_migrations)
    except BaseException:
        failed = True
        raise
    finally:
        if failed:
            try:
                await engine.dispose()
            except Exception:
                pass  # Preserve the primary error and its stage.
        else:
            stage = Stage.CLEANUP
            await engine.dispose()


try:
    if context.is_offline_mode():
        url = database_url(migration=True)
        stage = Stage.EXECUTION
        context.configure(
            url=url, literal_binds=True,
            dialect_opts={'paramstyle': 'named'}, **migration_options(),
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        run_async(run_online())
except ConfigurationError as exc:
    raise SystemExit(str(exc)) from None
except Exception as exc:
    raise SystemExit(format_error(exc, stage)) from None
