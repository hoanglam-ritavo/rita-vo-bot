"""Additive guard migration preserves notes, vectors, evidence and receipts."""
import importlib
from io import StringIO

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from src.database import Base, close_database, session_scope
from src.database.models import DocumentRevision
from src.database.runtime import run_async
from src.ingestion import approved_publication as publication
from test_structured_evidence import structured_package


def test_migration_sql_and_model_contract():
    migration = importlib.import_module('src.database.migrations.versions.c7d92e6a104b_publication_guards')
    assert migration.down_revision == 'bce4184a7f51'
    output = StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
    with Operations.context(context):
        migration.upgrade()
        migration.downgrade()
    sql = output.getvalue()
    assert 'CREATE EXTENSION IF NOT EXISTS vector' in sql
    assert 'revision_identity_immutable' in sql
    assert 'metadata_audit_append_only' in sql
    assert sql.count('CREATE TRIGGER') == sql.count('DROP TRIGGER') == 4
    assert all(forbidden not in sql for forbidden in ('DROP TABLE', 'DROP COLUMN', 'DROP EXTENSION', 'CASCADE'))
    assert 'evidence_notes' in DocumentRevision.__table__.c
    assert 'review_status' not in DocumentRevision.__table__.c
    assert 'review_notes' not in DocumentRevision.__table__.c


@pytest.mark.integration
def test_upgrade_downgrade_preserve_notes_vectors_and_history(empty_catalog_db, tmp_path, monkeypatch):
    path, _ = structured_package(tmp_path)
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights').write_bytes(b'synthetic')

    def embed(path, chunks, revision):
        for chunk in chunks:
            chunk['embedding'] = [1.] + [0.] * 383
        return chunks

    monkeypatch.setattr(publication, 'embed', embed)

    async def snapshot(publish=False):
        try:
            if publish:
                await publication.publish(tmp_path, path, 'a' * 40, model_path=model)
            async with session_scope() as db:
                return {table.name: list(await db.scalars(text(
                    f'SELECT row_to_json(t)::text FROM {table.name} AS t ORDER BY 1'
                ))) for table in Base.metadata.sorted_tables}
        finally:
            await close_database()

    before = run_async(snapshot(True))
    assert before['document_chunks'] and before['metadata_audits'] and before['evidence_items']
    config = Config('alembic.ini')
    try:
        for _ in range(2):
            command.downgrade(config, 'bce4184a7f51')
            assert run_async(snapshot()) == before
            command.upgrade(config, 'head')
            assert run_async(snapshot()) == before
    finally:
        command.upgrade(config, 'head')
