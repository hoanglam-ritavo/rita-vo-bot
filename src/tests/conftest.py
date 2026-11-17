"""Shared fixtures; database migrations run only when requested by a test."""
import os
import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import create_engine, func, select, text
from src.database import Base, close_database, session_scope
from src.database import models  # Register all tables before fixture cleanup.
from src.database.config import ROOT, validate_url
from src.database.runtime import run_async


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Keep default pytest temporary files in the repository workspace."""
    if config.option.basetemp is not None or 'PYTEST_DEBUG_TEMPROOT' in os.environ:
        return
    temporary_root = ROOT / '.tmp' / 'pytest-work'
    try:
        temporary_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise pytest.UsageError(
            f'Cannot create pytest temporary root {temporary_root}: {exc}'
        ) from exc
    environment = pytest.MonkeyPatch()
    config.add_cleanup(environment.undo)
    environment.setenv('PYTEST_DEBUG_TEMPROOT', str(temporary_root))


@pytest.fixture
def test_database(monkeypatch):
    load_dotenv(ROOT / '.env', override=False)
    value = os.environ.get('TEST_DATABASE_URL')
    if not value:
        pytest.skip('Set TEST_DATABASE_URL to a dedicated PostgreSQL test database.')
    url = validate_url(value, 'TEST_DATABASE_URL')
    for name in ('DATABASE_URL', 'MIGRATION_DATABASE_URL'):
        existing = os.environ.get(name)
        if existing:
            other = validate_url(existing, name)
            if (url.host, url.port or 5432, url.database) == (other.host, other.port or 5432, other.database):
                pytest.fail('TEST_DATABASE_URL must use a separate database.')
    monkeypatch.setenv('DATABASE_URL', value)
    monkeypatch.setenv('MIGRATION_DATABASE_URL', value)
    # Provision vector before the immutable baseline creates vector columns.
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
    finally:
        engine.dispose()
    command.upgrade(Config('alembic.ini'), 'head')
    command.upgrade(Config('alembic.ini'), 'head')


@pytest.fixture
def empty_catalog_db(test_database):
    async def check():
        try:
            async with session_scope() as db:
                for table in Base.metadata.sorted_tables:
                    assert await db.scalar(select(func.count()).select_from(table)) == 0, 'Catalog tests require empty application tables.'
        finally:
            await close_database()
    run_async(check())
    yield
    async def clean():
        try:
            async with session_scope() as db:
                names = ', '.join(table.name for table in reversed(Base.metadata.sorted_tables))
                # Test-only cleanup; append-only audit rows intentionally reject DELETE.
                await db.execute(text('TRUNCATE TABLE ' + names))
        finally:
            await close_database()
    run_async(clean())


@pytest.fixture
def baseline_catalog_db(test_database):
    """Exercise the applied baseline without additive publication guards."""
    async def check():
        try:
            async with session_scope() as db:
                for table in Base.metadata.sorted_tables:
                    assert await db.scalar(select(func.count()).select_from(table)) == 0
        finally:
            await close_database()
    run_async(check())
    command.downgrade(Config('alembic.ini'), 'bce4184a7f51')
    yield
    async def clean():
        try:
            async with session_scope() as db:
                names = ', '.join(table.name for table in reversed(Base.metadata.sorted_tables))
                await db.execute(text('TRUNCATE TABLE ' + names))
        finally:
            await close_database()
    run_async(clean())
    command.upgrade(Config('alembic.ini'), 'head')
