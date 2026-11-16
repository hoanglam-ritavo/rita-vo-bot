"""New baseline acceptance on a dedicated disposable PostgreSQL database only."""
import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, text, func, event
from sqlalchemy.exc import DBAPIError
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from src.database import Base, session_scope, close_database, get_engine
from src.database.models import (IngestionRun, PublicationAttempt, MetadataAudit, EvidenceValue,
                                EvidenceItem, ScopeItem, ScopeChunk, ItemChunk)
from src.database.runtime import run_async
from src.ingestion import approved_publication as publication, attempts
from src.ingestion.document_packages import Rejected
from src.ingestion.evidence import read_evidence
from src.retrieval.storage import search
from src.retrieval.reranking import profile_threshold
from test_structured_evidence import structured_package
from package_fixtures import set_metadata, revised_package

pytestmark = pytest.mark.integration


@pytest.fixture
def published_schema(empty_catalog_db, tmp_path, monkeypatch):
    path, _ = structured_package(tmp_path)
    set_metadata(path, access_groups=['public'])
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights').write_bytes(b'fictional model')
    def embed(*args):
        for chunk in args[1]:
            chunk['embedding'] = [1.] + [0.] * 383
        return args[1]
    monkeypatch.setattr(publication, 'embed', embed)
    return path, model


def test_fresh_schema_matches_orm(empty_catalog_db):
    async def scenario():
        try:
            async with get_engine().connect() as connection:
                def compare(sync):
                    return compare_metadata(MigrationContext.configure(sync, opts={'compare_type': True}), Base.metadata)
                assert await connection.run_sync(compare) == []
        finally:
            await close_database()
    run_async(scenario())


def test_publication_audit_receipts_and_metadata_fingerprint(published_schema, tmp_path):
    path, model = published_schema
    async def scenario():
        try:
            await publication.publish(tmp_path, path, 'a'*40, model_path=model, dry_run=True)
            async with session_scope() as db:
                assert await db.scalar(select(func.count()).select_from(PublicationAttempt)) == 0
                assert await db.scalar(select(func.count()).select_from(MetadataAudit)) == 0
            first = await publication.publish(tmp_path, path, 'a'*40, model_path=model)
            async with session_scope() as db:
                attempt = await db.get(PublicationAttempt, UUID(first['attempt_id']))
                assert attempt.status == 'succeeded' and attempt.outcome == 'published'
                assert attempt.started_at <= attempt.finished_at
                assert attempt.run_id == UUID(first['run_id'])
                audits = list(await db.scalars(select(MetadataAudit)))
                assert len(audits) == 1 and audits[0].before is None and audits[0].event == 'created'
                value = await db.scalar(select(EvidenceValue.numeric_value).limit(1))
                assert value == Decimal('100.01')
            fingerprint = publication.model_fingerprint(model)
            vector = [1.] + [0.] * 383
            before = await search(vector, 'a'*40, fingerprint, 5)
            set_metadata(path, applicability='Changed fictional audience')
            changed = await publication.publish(tmp_path, path, 'a'*40, model_path=model, update_metadata=True)
            assert changed['run_id'] == first['run_id']
            after = await search(vector, 'a'*40, fingerprint, 5)
            assert before['corpus_fingerprint'] != after['corpus_fingerprint']
            old_profile = dict(schema_version=2, fingerprint='pipeline', threshold=0., holdout_passed=True,
                               corpus_fingerprint=before['corpus_fingerprint'])
            assert profile_threshold(old_profile, 'pipeline', after['corpus_fingerprint']) is None
            skipped = await publication.publish(tmp_path, path, 'a'*40, model_path=model)
            async with session_scope() as db:
                assert await db.scalar(select(func.count()).select_from(MetadataAudit)) == 2
                assert await db.scalar(select(func.count()).select_from(IngestionRun)) == 1
                skip_attempt = await db.get(PublicationAttempt, UUID(skipped['attempt_id']))
                assert skip_attempt.outcome == 'unchanged'
            revised_package(tmp_path, path)
            set_metadata(path, schema_version=3)
            await publication.publish(tmp_path, path, 'a'*40, model_path=model)
            async with session_scope() as db:
                events = list(await db.scalars(select(MetadataAudit.event)))
                assert sorted(events) == ['created', 'created', 'superseded', 'updated']
        finally:
            await close_database()
    run_async(scenario())


@pytest.mark.parametrize('violation', ['model_null', 'failed_publication', 'role', 'cross_run',
                                     'duplicate_member', 'json_shape', 'source_identity', 'audit_update',
                                     'audit_delete', 'numeric_projection'])
def test_database_rejects_invalid_writes(published_schema, tmp_path, violation):
    path, model = published_schema
    async def scenario():
        try:
            first = await publication.publish(tmp_path, path, 'a'*40, model_path=model)
            second = await publication.publish(tmp_path, path, 'a'*40, model_path=model, force=True)
            parameters = {'run': UUID(first['run_id']), 'other': UUID(second['run_id'])}
            statements = {
                'model_null': 'UPDATE ingestion_runs SET model_name=NULL WHERE id=:run',
                'failed_publication': "UPDATE ingestion_runs SET status='failed', is_active=false WHERE id=:run",
                'role': "UPDATE document_chunks SET role='invented' WHERE run_id=:run",
                'cross_run': "INSERT INTO evidence_scope_items (run_id,scope_id,item_id,position) VALUES (:run,'all','missing-from-this-run',99)",
                'duplicate_member': "INSERT INTO evidence_scope_items (run_id,scope_id,item_id,position) VALUES (:run,'all','i0',99)",
                'json_shape': "UPDATE evidence_scopes SET data='[]'::jsonb WHERE run_id=:run",
                'source_identity': "UPDATE document_revisions SET sha256=repeat('b',64)",
                'audit_update': "UPDATE metadata_audits SET event='updated'",
                'audit_delete': 'DELETE FROM metadata_audits',
                'numeric_projection': 'UPDATE evidence_values SET numeric_value=999 WHERE run_id=:run',
            }
            if violation == 'cross_run':
                async with session_scope() as db:
                    # A valid item exists in another run only; the FK must still reject it.
                    db.add(EvidenceItem(run_id=parameters['other'], author_id='missing-from-this-run',
                        parent_id=None, data={'id': 'missing-from-this-run', 'parent_id': None}))
            with pytest.raises(DBAPIError):
                async with session_scope() as db:
                    await db.execute(text(statements[violation]), parameters)
        finally:
            await close_database()
    run_async(scenario())


def test_projection_drift_and_atomic_failure(published_schema, tmp_path):
    path, model = published_schema
    async def scenario():
        try:
            first = await publication.publish(tmp_path, path, 'a'*40, model_path=model)
            engine = get_engine().sync_engine
            def fail(connection, cursor, statement, *args):
                if statement.lstrip().lower().startswith('insert into evidence_scope_items'):
                    raise RuntimeError('fictional link insertion failure')
            event.listen(engine, 'before_cursor_execute', fail)
            try:
                with pytest.raises(RuntimeError, match='fictional link'):
                    await publication.publish(tmp_path, path, 'a'*40, model_path=model, force=True)
            finally:
                event.remove(engine, 'before_cursor_execute', fail)
            async with session_scope() as db:
                assert await db.scalar(select(func.count()).select_from(IngestionRun)) == 1
                assert await db.scalar(select(func.count()).select_from(MetadataAudit)) == 1
                statuses = list(await db.scalars(select(PublicationAttempt.status)))
                assert sorted(statuses) == ['failed', 'succeeded']
            async with session_scope() as db:
                await db.execute(text('DELETE FROM evidence_scope_items WHERE run_id=:run AND item_id=\'i0\''),
                                 {'run': UUID(first['run_id'])})
            with pytest.raises(Rejected, match='evidence_projection_mismatch'):
                async with session_scope() as db:
                    await read_evidence(db, UUID(first['run_id']))
        finally:
            await close_database()
    run_async(scenario())


def test_concurrent_publication_has_one_active_snapshot(published_schema, tmp_path):
    path, model = published_schema
    async def scenario():
        try:
            results = await asyncio.gather(*(
                publication.publish(tmp_path, path, 'a'*40, model_path=model, force=True)
                for _ in range(2)), return_exceptions=True)
            assert any(isinstance(result, dict) for result in results)
            assert all(isinstance(result, dict) or isinstance(result, Rejected) for result in results)
            async with session_scope() as db:
                assert await db.scalar(select(func.count()).select_from(IngestionRun).where(IngestionRun.is_active)) == 1
        finally:
            await close_database()
    run_async(scenario())


def test_lost_commit_acknowledgement_reconciles_without_republication(published_schema, tmp_path, monkeypatch):
    path, model = published_schema
    @asynccontextmanager
    async def lost_ack():
        async with session_scope() as db:
            yield db
        if db.info.get('publication_attempt_id'):
            raise RuntimeError('fictional lost commit acknowledgement')
    monkeypatch.setattr(publication, 'session_scope', lost_ack)
    async def scenario():
        try:
            with pytest.raises(attempts.AttemptUncertain) as error:
                await publication.publish(tmp_path, path, 'a'*40, model_path=model)
            attempt_id = UUID(error.value.attempt_id)
            async with session_scope() as db:
                attempt = await db.get(PublicationAttempt, attempt_id)
                assert attempt.status == 'running' and attempt.outcome == 'published'
                assert await db.scalar(select(func.count()).select_from(IngestionRun)) == 1
            await attempts.reconcile(attempt_id)
            async with session_scope() as db:
                attempt = await db.get(PublicationAttempt, attempt_id)
                assert attempt.status == 'succeeded' and attempt.finished_at is not None
                assert await db.scalar(select(func.count()).select_from(IngestionRun)) == 1
        finally:
            await close_database()
    run_async(scenario())
