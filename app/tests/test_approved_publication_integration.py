"""Publication transactions against the dedicated disposable PostgreSQL database only."""
from uuid import UUID
import pytest
from sqlalchemy import select, func
from app.database import session_scope, close_database
from app.database.models import DocumentRevision, IngestionRun, DocumentChunk
from app.database.runtime import run_async
from app.ingestion import approved_publication as service
from app.ingestion import document_packages as p
from test_document_packages import approved

pytestmark = pytest.mark.integration


def test_atomic_publication_metadata_conflict_and_failure(empty_catalog_db, tmp_path, monkeypatch):
    _, _, review, approval = approved(tmp_path)
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights').write_bytes(b'synthetic')
    def embed(path, chunks, revision):
        for c in chunks:
            c['embedding'] = [1.0] + [0.0]*383
        return chunks
    monkeypatch.setattr(service, 'embed', embed)
    async def scenario():
        try:
            result = await service.publish(tmp_path, approval, 'a'*40, model_path=model)
            assert result['code'] == 'published'
            assert (await service.publish(tmp_path, approval, 'a'*40, model_path=model))['status'] == 'skipped'
            record = p.metadata(review)
            record['owner'] = 'Changed synthetic owner'
            (review / 'metadata.json').write_bytes(p.encoded(record))
            second = p.approve(tmp_path, review, 'Reviewer')
            with pytest.raises(p.Rejected, match='metadata_conflict'):
                await service.publish(tmp_path, second, 'a'*40, model_path=model)
            def fail(*args):
                raise RuntimeError('synthetic embedding failure')
            monkeypatch.setattr(service, 'embed', fail)
            with pytest.raises(RuntimeError):
                await service.publish(tmp_path, second, 'a'*40, model_path=model, update_metadata=True)
            async with session_scope() as db:
                revision = await db.get(DocumentRevision, UUID(record['revision_id']))
                assert revision.owner != record['owner']
                assert await db.scalar(select(func.count()).select_from(IngestionRun).where(IngestionRun.is_active.is_(True))) == 1
                assert await db.scalar(select(func.count()).select_from(DocumentChunk)) > 0
            monkeypatch.setattr(service, 'embed', embed)
            await service.publish(tmp_path, second, 'a'*40, model_path=model, update_metadata=True)
            async with session_scope() as db:
                assert (await db.get(DocumentRevision, UUID(record['revision_id']))).owner == record['owner']
                assert await db.scalar(select(func.count()).select_from(IngestionRun).where(IngestionRun.is_active.is_(True))) == 1
        finally:
            await close_database()
    run_async(scenario())
