"""Synthetic tests only on the guarded disposable TEST_DATABASE_URL."""
import asyncio
import copy
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.validation import new_record
from app.catalog.importer import ImportRejected, synchronize
from app.database import Base, close_database, get_engine, session_scope
from app.database.models import (Document, DocumentRevision, DocumentAccessRule,
                           DocumentRelation, IngestionRun, DocumentChunk)
from app.database.runtime import run_async

pytestmark = pytest.mark.integration


def snapshot():
    records = [new_record(f'synthetic/{i}.pdf', {'sha256': 'a' * 64, 'size_bytes': 10, 'extension': '.pdf'})
               for i in range(2)]
    records[0]['access_groups'] = ['staff']
    records[0]['relations'] = [{'type': 'duplicate_of', 'revision_id': records[1]['revision_id']}]
    records[1]['indexing_decision'] = 'exclude'
    return {'schema_version': 1, 'documents': records}


def test_import_lifecycle(empty_catalog_db):
    async def exercise():
        try:
            catalog = snapshot()
            ids = [UUID(r['revision_id']) for r in catalog['documents']]
            preview = await synchronize(catalog, dry_run=True)
            assert preview.revisions_added == 2
            async with session_scope() as db:
                assert await db.scalar(select(func.count()).select_from(Document)) == 0
            result = await synchronize(catalog)
            assert result.documents_added == result.revisions_added == 2
            async with session_scope() as db:
                timestamps = [(r.id, r.updated_at) for r in (await db.scalars(select(DocumentRevision))).all()]
                assert await db.scalar(select(func.count()).select_from(IngestionRun)) == 0
                assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
            repeated = await synchronize(catalog)
            assert repeated.revisions_updated == repeated.revisions_added == 0
            async with session_scope() as db:
                assert sorted(timestamps) == sorted((r.id, r.updated_at) for r in (await db.scalars(select(DocumentRevision))).all())
            # Swap paths without transient unique-key violations; change ACL and relations.
            a, b = catalog['documents']
            a['source_path'], b['source_path'] = b['source_path'], a['source_path']
            a.update(title='Updated synthetic title', access_groups=['hr'], relations=[])
            assert (await synchronize(catalog)).revisions_updated == 2
            async with session_scope() as db:
                assert (await db.get(DocumentRevision, ids[0])).title == 'Updated synthetic title'
                assert list(await db.scalars(select(DocumentAccessRule.group_name))) == ['hr']
                assert await db.scalar(select(func.count()).select_from(DocumentRelation)) == 0
            conflict = copy.deepcopy(catalog)
            conflict['documents'][0]['sha256'] = 'b' * 64
            with pytest.raises(ImportRejected):
                await synchronize(conflict)
            a['revision_id'] = str(uuid4())
            a['sha256'] = 'b' * 64
            catalog['documents'] = [a]
            result = await synchronize(catalog)
            assert (result.revisions_added, result.revisions_retired) == (1, 2)
            async with session_scope() as db:
                assert await db.scalar(select(func.count()).select_from(DocumentRevision)) == 3
                for rid in ids:
                    assert not (await db.get(DocumentRevision, rid)).is_catalog_current
                assert (await db.get(DocumentRevision, UUID(a['revision_id']))).is_catalog_current
        finally:
            await close_database()
    run_async(exercise())


def test_concurrent_import_and_rollback(empty_catalog_db, monkeypatch):
    async def exercise():
        try:
            catalog = snapshot()
            results = await asyncio.gather(synchronize(catalog), synchronize(catalog))
            assert sum(r.revisions_added for r in results) == 2
            assert sum(r.revisions_updated for r in results) == 0
            modified = copy.deepcopy(catalog)
            modified['documents'][0]['title'] = 'Must roll back'
            original = AsyncSession.flush
            calls = 0
            async def failing_flush(self, *args, **kwargs):
                nonlocal calls
                await original(self, *args, **kwargs)
                calls += 1
                if calls == 3:
                    raise RuntimeError('Injected after metadata writes')
            with monkeypatch.context() as patch:
                patch.setattr(AsyncSession, 'flush', failing_flush)
                with pytest.raises(RuntimeError):
                    await synchronize(modified)
            async with session_scope() as db:
                revision = await db.get(DocumentRevision, UUID(catalog['documents'][0]['revision_id']))
                assert revision.title == catalog['documents'][0]['title']
                assert revision.is_catalog_current
        finally:
            await close_database()
    run_async(exercise())


def test_constraints_vectors_and_metadata(empty_catalog_db):
    async def exercise():
        try:
            catalog = snapshot()
            await synchronize(catalog)
            revision_id = UUID(catalog['documents'][0]['revision_id'])
            run_id = uuid4()
            async with session_scope() as db:
                db.add(IngestionRun(id=run_id, revision_id=revision_id, source_sha256='a' * 64,
                                    status='succeeded', is_active=True, embedding_dimensions=384))
            async with session_scope() as db:
                db.add(DocumentChunk(run_id=run_id, chunk_index=0, content='Synthetic clause',
                                     source_location={'page_start': 1, 'page_end': 1},
                                     extraction_method='native', embedding=[1.0] + [0.0] * 383))
            async with session_scope() as db:
                vector = await db.scalar(select(DocumentChunk.embedding))
                assert list(vector) == pytest.approx([1.0] + [0.0] * 383)
            bad_objects = [
                IngestionRun(revision_id=revision_id, source_sha256='a' * 64, status='succeeded', is_active=True),
                IngestionRun(revision_id=revision_id, source_sha256='a' * 64, status='pending', is_active=True),
                DocumentChunk(run_id=run_id, chunk_index=0, content='Duplicate', source_location={}, extraction_method='native'),
                DocumentChunk(run_id=run_id, chunk_index=1, content='Wrong dimensions', source_location={}, extraction_method='native', embedding=[1, 0]),
                DocumentRelation(source_revision_id=revision_id, target_revision_id=uuid4(), relation_type='duplicate_of'),
                DocumentRelation(source_revision_id=revision_id, target_revision_id=revision_id, relation_type='duplicate_of'),
            ]
            for obj in bad_objects:
                with pytest.raises((IntegrityError, StatementError)):
                    async with session_scope() as db:
                        db.add(obj)
            async with get_engine().connect() as connection:
                def check_metadata(sync_connection):
                    context = MigrationContext.configure(sync_connection)
                    # Alembic version is ignored by autogeneration.
                    assert compare_metadata(context, Base.metadata) == []
                await connection.run_sync(check_metadata)
        finally:
            await close_database()
    run_async(exercise())


def test_migration_roundtrip_preserves_external_table(empty_catalog_db):
    table = 'test_external_' + uuid4().hex
    async def execute(sql):
        try:
            async with get_engine().begin() as connection:
                await connection.execute(text(sql))
        finally:
            await close_database()
    run_async(execute(f'CREATE TABLE {table} (id integer)'))
    try:
        config = Config('alembic.ini')
        command.downgrade(config, '0001_enable_vector')
        command.upgrade(config, 'head')
        command.upgrade(config, 'head')
        async def verify():
            try:
                async with session_scope() as db:
                    assert await db.scalar(text('SELECT to_regclass(:name)'), {'name': table}) == table
                    assert await db.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'")) == 1
            finally:
                await close_database()
        run_async(verify())
    finally:
        run_async(execute(f'DROP TABLE {table}'))
