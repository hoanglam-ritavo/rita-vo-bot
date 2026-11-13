"""Migration SQL checks and disposable-PostgreSQL preservation coverage."""
import importlib
from io import StringIO
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from src.database import close_database, session_scope
from src.database.models import DocumentRevision
from src.database.runtime import run_async


def test_migration_sql_and_model_contract():
    migration = importlib.import_module('src.database.migrations.versions.0003_agent_packages')
    output = StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
    with Operations.context(context):
        migration.upgrade()
        migration.downgrade()
    sql = output.getvalue()
    assert 'DROP COLUMN review_status' in sql
    assert 'RENAME review_notes TO evidence_notes' in sql
    assert 'RENAME evidence_notes TO review_notes' in sql
    assert "DEFAULT 'pending' NOT NULL" in sql
    assert 'DROP TABLE' not in sql and 'EXTENSION' not in sql
    assert 'evidence_notes' in DocumentRevision.__table__.c
    assert 'review_status' not in DocumentRevision.__table__.c
    assert 'review_notes' not in DocumentRevision.__table__.c


@pytest.mark.integration
def test_upgrade_downgrade_preserve_notes_vectors_and_history(empty_catalog_db):
    """The shared fixture guards the dedicated test URL before any migrations."""
    config = Config('alembic.ini')
    doc, rev, run, chunk = [uuid4() for _ in range(4)]
    params = {'doc': doc, 'rev': rev, 'run': run, 'chunk': chunk,
              'vector': '[' + ','.join(['1'] + ['0'] * 383) + ']'}

    async def seed():
        try:
            async with session_scope() as db:
                await db.execute(text('INSERT INTO documents (id) VALUES (:doc)'), params)
                await db.execute(text("""INSERT INTO document_revisions
                    (id, document_id, source_path, source_path_key, sha256, size_bytes, extension,
                     review_status, review_notes) VALUES
                    (:rev, :doc, 'synthetic.txt', 'synthetic.txt', repeat('a',64), 1, '.txt',
                     'reviewed', '["Synthetic evidence"]'::jsonb)"""), params)
                await db.execute(text("""INSERT INTO ingestion_runs
                    (id, revision_id, status, is_active, source_sha256, chunk_count, configuration)
                    VALUES (:run, :rev, 'succeeded', true, repeat('a',64), 1,
                    '{"approval_id":"historical-only"}'::jsonb)"""), params)
                await db.execute(text("""INSERT INTO document_chunks
                    (id, run_id, chunk_index, content, source_location, extraction_method, embedding)
                    VALUES (:chunk, :run, 0, 'Synthetic text', '{}'::jsonb, 'native', CAST(:vector AS vector))"""), params)
        finally:
            await close_database()

    async def verify(upgraded):
        try:
            async with session_scope() as db:
                column = 'evidence_notes' if upgraded else 'review_notes'
                assert await db.scalar(text(f'SELECT {column} FROM document_revisions WHERE id=:rev'), params) == ['Synthetic evidence']
                if not upgraded:
                    assert await db.scalar(text('SELECT review_status FROM document_revisions WHERE id=:rev'), params) == 'pending'
                assert await db.scalar(text('SELECT embedding::text FROM document_chunks WHERE id=:chunk'), params) == params['vector']
                config_value = await db.scalar(text('SELECT configuration FROM ingestion_runs WHERE id=:run'), params)
                assert config_value == {'approval_id': 'historical-only'}
                assert await db.scalar(text('SELECT is_active FROM ingestion_runs WHERE id=:run'), params) is True
        finally:
            await close_database()

    command.downgrade(config, '0002_type_a_catalog')
    try:
        run_async(seed())
        command.upgrade(config, 'head')
        run_async(verify(True))
        command.downgrade(config, '0002_type_a_catalog')
        run_async(verify(False))
    finally:
        # Restore head before the shared fixture cleans up via current models.
        command.upgrade(config, 'head')
