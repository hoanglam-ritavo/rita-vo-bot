"""Uses only the dedicated TEST_DATABASE_URL guarded by shared fixtures."""
import asyncio
from uuid import UUID

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.importer import synchronize
from app.database import session_scope, close_database
from app.database.models import IngestionRun, DocumentChunk
from app.database.runtime import run_async
from app.ingestion import approved_publication as service
from app.ingestion import document_packages as packages
from test_document_packages import approved
from test_approved_publication import fake_embed

pytestmark = pytest.mark.integration


def test_corrected_json_roundtrip_skip_force_and_rollback(empty_catalog_db, tmp_path, monkeypatch):
    record, _, _, approval = approved(tmp_path)
    monkeypatch.setattr(service, 'embed', fake_embed)
    monkeypatch.setattr(service, 'version', lambda name: 'synthetic')

    async def exercise():
        try:
            await synchronize({'schema_version': 1, 'documents': [record]})
            first = await service.publish(tmp_path, approval, 'a' * 40)
            second = await service.publish(tmp_path, approval, 'a' * 40)
            assert second['status'] == 'skipped' and second['run_id'] == first['run_id']
            async with session_scope() as db:
                chunks = list(await db.scalars(select(DocumentChunk).order_by(DocumentChunk.chunk_index)))
                assert chunks[0].content == 'Human corrected policy.'
                assert chunks[0].source_location['spans'][0]['location']['line_start'] == 1
                count = len(chunks)
            original = AsyncSession.flush
            async def fail_after_insert(db, *args, **kwargs):
                inserting = any(isinstance(obj, DocumentChunk) for obj in db.new)
                await original(db, *args, **kwargs)
                if inserting:
                    raise RuntimeError('Synthetic insert failure')
            with monkeypatch.context() as patch:
                patch.setattr(AsyncSession, 'flush', fail_after_insert)
                with pytest.raises(RuntimeError):
                    await service.publish(tmp_path, approval, 'a' * 40, force=True)
            async with session_scope() as db:
                active = await service.lifecycle.active_run(db, UUID(record['revision_id']))
                assert str(active.id) == first['run_id']
                assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == count
        finally:
            await close_database()
    run_async(exercise())


def test_stale_metadata_and_dry_run_no_writes(empty_catalog_db, tmp_path, monkeypatch):
    record, _, _, approval = approved(tmp_path)
    monkeypatch.setattr(service, 'embed', lambda *a: pytest.fail('Model must not run'))
    async def exercise():
        try:
            with pytest.raises(service.lifecycle.PublishRejected):
                await service.publish(tmp_path, approval, 'a' * 40, dry_run=True)
            await synchronize({'schema_version': 1, 'documents': [record]})
            assert (await service.publish(tmp_path, approval, 'a' * 40, dry_run=True))['code'] == 'dry_run'
            async with session_scope() as db:
                assert await db.scalar(select(func.count()).select_from(IngestionRun)) == 0
                assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
        finally:
            await close_database()
    run_async(exercise())


def test_approved_activation_race_keeps_single_winner(empty_catalog_db, tmp_path):
    record, _, _, approval = approved(tmp_path)
    _, payload, _, _ = packages.load_approval(tmp_path, approval)
    config = service.configuration(payload, 'a' * 40)
    chunks = fake_embed(tmp_path, payload['chunks'], 'a' * 40)
    async def exercise():
        try:
            await synchronize({'schema_version': 1, 'documents': [record]})
            attempts = await asyncio.gather(*[service.lifecycle.start_run(record, config, True) for _ in range(2)])
            results = await asyncio.gather(*[
                service.lifecycle.activate_run(record, run, previous, chunks) for run, previous in attempts], return_exceptions=True)
            assert sum(r is None for r in results) == 1
            assert sum(isinstance(r, service.lifecycle.PublishRejected) for r in results) == 1
            async with session_scope() as db:
                assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == len(chunks)
        finally:
            await close_database()
    run_async(exercise())
