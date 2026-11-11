"""Publication transactions against the dedicated, guarded PostgreSQL test DB."""
import asyncio
import copy
from uuid import UUID

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ingestion_helpers import setup_source, options, chunk
from app.catalog.importer import synchronize
from app.database import close_database, session_scope
from app.database.models import IngestionRun, DocumentChunk
from app.database.runtime import run_async
from app.ingestion import publish as producer

pytestmark = pytest.mark.integration
CONFIG = {'model_name': 'synthetic', 'model_revision': 'a' * 40}


def test_publish_skip_force_and_artifacts(empty_catalog_db, tmp_path, monkeypatch):
    record, _ = setup_source(tmp_path)
    monkeypatch.setattr(producer, 'processing_configuration', lambda *a: (CONFIG, None))
    def process(root, snapshot, record, args, configuration, tessdata, directory):
        assert snapshot.read_bytes() == b'synthetic PDF bytes'
        (directory / 'chunks.jsonl').write_text('synthetic artifact')
        (directory / 'manifest.json').write_text('{}')
        return [chunk()]
    monkeypatch.setattr(producer, 'process_snapshot', process)
    async def exercise():
        try:
            await synchronize({'schema_version': 1, 'documents': [record]})
            first = await producer.publish(tmp_path, options(record))
            second = await producer.publish(tmp_path, options(record))
            assert first['status'] == 'published' and second['status'] == 'skipped'
            assert first['run_id'] == second['run_id']
            third = await producer.publish(tmp_path, options(record, force=True))
            async with session_scope() as db:
                active = await producer.active_run(db, UUID(record['revision_id']))
                assert str(active.id) == third['run_id'] and active.status == 'succeeded'
                assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 2
                assert list(await db.scalar(select(DocumentChunk.embedding).limit(1))) == pytest.approx(chunk()['embedding'])
        finally:
            await close_database()
    run_async(exercise())


def test_concurrent_publication_preserves_first_winner(empty_catalog_db, tmp_path):
    record, _ = setup_source(tmp_path)
    async def exercise():
        try:
            await synchronize({'schema_version': 1, 'documents': [record]})
            attempts = await asyncio.gather(producer.start_run(record, CONFIG, True), producer.start_run(record, CONFIG, True))
            results = await asyncio.gather(*(
                producer.finish_run(tmp_path, record, run_id, previous, [chunk()])
                for run_id, previous in attempts), return_exceptions=True)
            assert sum(result is None for result in results) == 1
            assert sum(isinstance(result, producer.PublishRejected) for result in results) == 1
            async with session_scope() as db:
                assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 1
                active = await producer.active_run(db, UUID(record['revision_id']))
                assert active.id == attempts[results.index(None)][0]
        finally:
            await close_database()
    run_async(exercise())


def test_chunk_write_rollback_preserves_previous_active(empty_catalog_db, tmp_path, monkeypatch):
    record, _ = setup_source(tmp_path)
    async def exercise():
        try:
            await synchronize({'schema_version': 1, 'documents': [record]})
            first, previous = await producer.start_run(record, CONFIG, True)
            await producer.finish_run(tmp_path, record, first, previous, [chunk()])
            second, previous = await producer.start_run(record, CONFIG, True)
            original = AsyncSession.flush
            async def fail_after_write(db, *args, **kwargs):
                has_chunks = any(isinstance(obj, DocumentChunk) for obj in db.new)
                await original(db, *args, **kwargs)
                if has_chunks:
                    raise RuntimeError('Injected after chunk write')
            with monkeypatch.context() as patch:
                patch.setattr(AsyncSession, 'flush', fail_after_write)
                with pytest.raises(RuntimeError):
                    await producer.finish_run(tmp_path, record, second, previous, [chunk()])
            await producer.fail_run(second, 'processing_failed')
            async with session_scope() as db:
                assert (await producer.active_run(db, UUID(record['revision_id']))).id == first
                assert (await db.get(IngestionRun, second)).status == 'failed'
                assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 1
        finally:
            await close_database()
    run_async(exercise())


@pytest.mark.parametrize('change', ['metadata', 'retire'])
def test_import_changes_prevent_publication(empty_catalog_db, tmp_path, change):
    record, _ = setup_source(tmp_path)
    async def exercise():
        try:
            catalog = {'schema_version': 1, 'documents': [record]}
            await synchronize(catalog)
            run_id, previous = await producer.start_run(record, CONFIG, True)
            changed = copy.deepcopy(catalog)
            if change == 'metadata':
                changed['documents'][0]['access_groups'] = ['different-group']
            else:
                changed['documents'] = []
            await synchronize(changed)
            with pytest.raises(producer.PublishRejected):
                await producer.finish_run(tmp_path, record, run_id, previous, [chunk()])
            async with session_scope() as db:
                assert await producer.active_run(db, UUID(record['revision_id'])) is None
                assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
        finally:
            await close_database()
    run_async(exercise())


def test_import_and_ingestion_share_lock(empty_catalog_db, tmp_path):
    record, _ = setup_source(tmp_path)
    async def exercise():
        tasks = []
        try:
            catalog = {'schema_version': 1, 'documents': [record]}
            await synchronize(catalog)
            async with session_scope() as db:
                await producer.lock(db)
                tasks = [asyncio.create_task(synchronize(catalog)),
                         asyncio.create_task(producer.start_run(record, CONFIG, False))]
                done, _ = await asyncio.wait(tasks, timeout=0.2)
                assert not done
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await close_database()
    run_async(exercise())
