"""Run-owned evidence on disposable PostgreSQL; no real document content."""
from decimal import Decimal
from uuid import UUID
import pytest
from sqlalchemy import select, event
from src.database import session_scope, close_database, get_engine
from src.database.models import EvidenceValue, IngestionRun
from src.database.runtime import run_async
from src.ingestion import approved_publication as publication
from src.ingestion import document_packages as p
from src.retrieval import evidence_storage
from test_structured_evidence import structured_package
from package_fixtures import set_metadata

pytestmark = pytest.mark.integration


def test_structured_atomicity_and_visibility(empty_catalog_db, tmp_path, monkeypatch):
    path, _ = structured_package(tmp_path)
    set_metadata(path, access_groups=['public'])
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights').write_bytes(b'synthetic')
    def embed(path, chunks, revision):
        for c in chunks:
            c['embedding'] = [1.0] + [0.0]*383
        return chunks
    monkeypatch.setattr(publication, 'embed', embed)
    async def scenario():
        try:
            first = await publication.publish(tmp_path, path, 'a'*40, model_path=model)
            fingerprint = publication.model_fingerprint(model)
            candidates = await evidence_storage.candidates([1.0]+[0.0]*383, 'a'*40, fingerprint)
            assert len(candidates['scopes']) == 1
            b = await evidence_storage.expand(candidates['scopes'][0], 'a'*40, fingerprint)
            assert len(b['items']) == len(b['values']) == len(b['matches']) == 2
            detail = await evidence_storage.detail(b['matches'], 'a'*40, fingerprint)
            assert len(detail['required_ids']) == 1 and len(detail['matches']) == 2
            from src.retrieval.storage import search
            raw = await search([1.0]+[0.0]*383, 'a'*40, fingerprint, 5)
            focused = await search([1.0]+[0.0]*383, 'a'*40, fingerprint, 5, detail_only=True)
            assert len(raw['matches']) == 2 and len(focused['matches']) == 1
            assert focused['matches'][0]['role'] == 'detail'
            async with session_scope() as db:
                assert await db.scalar(select(EvidenceValue.numeric_value).limit(1)) == Decimal('100.01')
            data = p.decode((path / 'chunks.json').read_bytes())
            data['values'][0]['value'] = '200.00'
            (path / 'chunks.json').write_bytes(p.encoded(data))
            engine = get_engine().sync_engine
            def fail(connection, cursor, statement, parameters, context, executemany):
                if statement.lstrip().lower().startswith('insert into evidence_values'):
                    raise RuntimeError('synthetic structured insert failure')
            event.listen(engine, 'before_cursor_execute', fail)
            try:
                with pytest.raises(RuntimeError, match='synthetic structured'):
                    await publication.publish(tmp_path, path, 'a'*40, model_path=model)
            finally:
                event.remove(engine, 'before_cursor_execute', fail)
            async with session_scope() as db:
                runs = list(await db.scalars(select(IngestionRun)))
                assert len(runs) == 1 and runs[0].id == UUID(first['run_id']) and runs[0].is_active
            set_metadata(path, access_groups=['private'])
            await publication.publish(tmp_path, path, 'a'*40, model_path=model, update_metadata=True)
            assert not (await evidence_storage.candidates([1.0]+[0.0]*383, 'a'*40, fingerprint))['scopes']
            with pytest.raises(ValueError, match='unavailable'):
                await evidence_storage.expand(candidates['scopes'][0], 'a'*40, fingerprint)
        finally:
            await close_database()
    run_async(scenario())


def test_v3_downgrade_preserves_vectors_and_shared_extension(empty_catalog_db, tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    path, _ = structured_package(tmp_path)
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights').write_bytes(b'synthetic')
    def embed(path, chunks, revision):
        for c in chunks:
            c['embedding'] = [1.0] + [0.0]*383
        return chunks
    monkeypatch.setattr(publication, 'embed', embed)
    async def publish_and_read(publish=False):
        try:
            if publish:
                await publication.publish(tmp_path, path, 'a'*40, model_path=model)
            async with session_scope() as db:
                chunks = (await db.execute(text('SELECT id, content, embedding::text FROM document_chunks ORDER BY id'))).all()
                assert await db.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'")) == 1
                return chunks
        finally:
            await close_database()
    before = run_async(publish_and_read(True))
    try:
        command.downgrade(Config('alembic.ini'), '0004_updated_at_triggers')
        assert run_async(publish_and_read()) == before
    finally:
        command.upgrade(Config('alembic.ini'), 'head')
