"""Timestamp SQL contract and behavior on guarded disposable PostgreSQL only."""
import importlib
from datetime import datetime, timezone
from io import StringIO
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import FetchedValue, text

from app.database import close_database, session_scope
from app.database.models import Document, DocumentRevision
from app.database.runtime import run_async

OLD = datetime(2000, 1, 1, tzinfo=timezone.utc)


def test_timestamp_migration_contract():
    migration = importlib.import_module('app.database.migrations.versions.0004_updated_at_triggers')
    assert migration.down_revision == '0003_agent_packages'
    output = StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
    with Operations.context(context):
        migration.upgrade()
        migration.downgrade()
    sql = output.getvalue()
    assert sql.count('CREATE TRIGGER') == 2
    assert sql.count('DROP TRIGGER') == 2
    for table in ('documents', 'document_revisions'):
        assert f'BEFORE UPDATE ON {table}' in sql
        assert f'DROP TRIGGER rita_vo_{table}_updated_at ON {table}' in sql
    assert 'NEW.updated_at = statement_timestamp()' in sql
    assert sql.index('CREATE FUNCTION') < sql.index('CREATE TRIGGER')
    assert sql.rindex('DROP TRIGGER') < sql.index('DROP FUNCTION')
    for forbidden in ('DROP TABLE', 'DELETE FROM', 'ALTER TABLE', 'EXTENSION', 'CASCADE'):
        assert forbidden not in sql
    for model in (Document, DocumentRevision):
        column = model.__table__.c.updated_at
        assert isinstance(column.server_onupdate, FetchedValue)
        assert column.onupdate is None
        assert column.server_default is not None


async def seed(old=True):
    async with session_scope() as db:
        timestamps = {'created_at': OLD, 'updated_at': OLD} if old else {}
        document = Document(id=uuid4(), **timestamps)
        db.add(document)
        await db.flush()
        revision = DocumentRevision(
            id=uuid4(), document_id=document.id, source_path='synthetic.txt',
            source_path_key='synthetic.txt', sha256='a' * 64,
            size_bytes=1, extension='.txt', **timestamps,
        )
        db.add(revision)
        await db.flush()
        for row in (document, revision):
            await db.refresh(row)
            assert row.created_at is not None
            assert row.updated_at == row.created_at
        return document.id, revision.id


@pytest.mark.integration
def test_timestamp_updates_and_rollback(empty_catalog_db):
    async def scenario():
        try:
            await seed(old=False)
            ids = await seed()
            for model, row_id in zip((Document, DocumentRevision), ids):
                async with session_scope() as db:
                    row = await db.get(model, row_id)
                    # Forces an ORM UPDATE even on the identity-only Document model.
                    row.updated_at = datetime(1999, 1, 1, tzinfo=timezone.utc)
                    if model is DocumentRevision:
                        row.title = 'Changed through ORM'
                    await db.flush()
                    await db.refresh(row, ['updated_at', 'created_at'])
                    assert row.updated_at > OLD
                    assert row.created_at == OLD
                    stored = await db.scalar(text(
                        f'SELECT updated_at FROM {model.__tablename__} WHERE id=:id'
                    ), {'id': row_id})
                    assert row.updated_at == stored

                async with session_scope() as db:
                    # No business-value change and a forged timestamp are both covered.
                    result = (await db.execute(text(f'''
                        UPDATE {model.__tablename__} SET id=id, updated_at=:old
                        WHERE id=:id
                        RETURNING updated_at, created_at, statement_timestamp() AS expected
                    '''), {'id': row_id, 'old': OLD})).one()
                    assert result.updated_at == result.expected
                    assert result.created_at == OLD
                    committed_timestamp = result.updated_at

                with pytest.raises(RuntimeError, match='rollback'):
                    async with session_scope() as db:
                        assignment = "title='Rolled back'" if model is DocumentRevision else 'id=id'
                        await db.execute(text(f'UPDATE {model.__tablename__} SET {assignment} WHERE id=:id'), {'id': row_id})
                        raise RuntimeError('rollback')
                async with session_scope() as db:
                    row = await db.get(model, row_id)
                    assert row.updated_at == committed_timestamp
                    assert row.created_at == OLD
                    if model is DocumentRevision:
                        assert row.title == 'Changed through ORM'
        finally:
            await close_database()
    run_async(scenario())


@pytest.mark.integration
def test_timestamp_migration_preserves_rows(empty_catalog_db):
    config = Config('alembic.ini')

    async def seed_closed():
        try:
            return await seed()
        finally:
            await close_database()

    async def verify(ids, enabled):
        try:
            async with session_scope() as db:
                for model, row_id in zip((Document, DocumentRevision), ids):
                    row = await db.get(model, row_id)
                    assert row.created_at == row.updated_at == OLD
                    if model is DocumentRevision:
                        assert row.sha256 == 'a' * 64 and row.source_path == 'synthetic.txt'
                    result = (await db.execute(text(f'''
                        UPDATE {model.__tablename__} SET updated_at=:old WHERE id=:id
                        RETURNING updated_at, statement_timestamp() AS expected
                    '''), {'id': row_id, 'old': OLD})).one()
                    assert result.updated_at == (result.expected if enabled else OLD)
                # Keep the original timestamps for the next migration assertion.
                raise RuntimeError('rollback verification')
        finally:
            await close_database()

    command.downgrade(config, '0003_agent_packages')
    try:
        ids = run_async(seed_closed())
        for enabled in (True, False, True):
            if enabled:
                command.upgrade(config, 'head')
            else:
                command.downgrade(config, '0003_agent_packages')
            with pytest.raises(RuntimeError, match='rollback verification'):
                run_async(verify(ids, enabled))
    finally:
        command.upgrade(config, 'head')
