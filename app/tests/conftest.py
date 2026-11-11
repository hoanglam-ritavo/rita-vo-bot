"""Shared fixtures; database migrations run only when requested by a test."""
import os
import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import delete, func, select
from app.db import Base, close_database, session_scope
from app.db import models  # Register all tables before fixture cleanup.
from app.db.config import ROOT, validate_url
from app.db.runtime import run_async


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
                for table in reversed(Base.metadata.sorted_tables):
                    await db.execute(delete(table))
        finally:
            await close_database()
    run_async(clean())
