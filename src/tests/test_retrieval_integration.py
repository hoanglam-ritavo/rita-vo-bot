"""Synthetic vector ordering/filters on a dedicated disposable PostgreSQL database."""
from datetime import date, timedelta
from uuid import UUID
import pytest
from sqlalchemy import select, delete
from src.database import session_scope, close_database
from src.database.runtime import run_async
from src.database.models import DocumentRevision, DocumentAccessRule, DocumentChunk, IngestionRun
from src.ingestion import approved_publication as publication
from src.ingestion import document_packages as p
from src.retrieval.storage import search
from package_fixtures import ready

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('change,expected', [
    ('none', 'matches_found'), ('private', 'no_eligible_documents'),
    ('inactive_run', 'no_eligible_documents'), ('failed_run', 'no_eligible_documents'),
    ('not_current', 'no_eligible_documents'), ('hold', 'no_eligible_documents'),
    ('expired', 'no_eligible_documents'), ('future', 'no_eligible_documents'),
    ('withdrawn', 'no_eligible_documents'), ('model_name', 'model_mismatch'),
    ('model_revision', 'model_mismatch'), ('fingerprint', 'model_mismatch'),
    ('normalization', 'model_mismatch'), ('prefix', 'model_mismatch'),
    ('no_vectors', 'no_matches'), ('boundary_dates', 'matches_found'),
])
def test_public_search(empty_catalog_db, tmp_path, monkeypatch, change, expected):
    package, _ = ready(tmp_path)
    record = p.metadata(package)
    record['access_groups'] = ['public', 'staff']
    (package / 'metadata.json').write_bytes(p.encoded(record))
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights').write_bytes(b'synthetic')
    def embed(path, chunks, revision):
        for index, chunk in enumerate(chunks):
            chunk['embedding'] = ([1.0, 0.0] if index == 0 else [0.0, 1.0]) + [0.0] * 382
        return chunks
    monkeypatch.setattr(publication, 'embed', embed)

    async def scenario():
        try:
            result = await publication.publish(tmp_path, package, 'a'*40, model_path=model)
            async with session_scope() as db:
                run = await db.get(IngestionRun, UUID(result['run_id']))
                revision = await db.get(DocumentRevision, run.revision_id)
                if change == 'private':
                    await db.execute(delete(DocumentAccessRule).where(DocumentAccessRule.group_name == 'public'))
                elif change == 'inactive_run': run.is_active = False
                elif change == 'failed_run': run.is_active, run.status = False, 'failed'
                elif change == 'not_current': revision.is_catalog_current = False
                elif change == 'hold': revision.indexing_decision = 'hold'
                elif change == 'withdrawn': revision.lifecycle_status = 'withdrawn'
                elif change == 'expired': revision.effective_to = date.today() - timedelta(days=1)
                elif change == 'future': revision.effective_from = date.today() + timedelta(days=1)
                elif change == 'boundary_dates': revision.effective_from = revision.effective_to = date.today()
                elif change == 'model_name': run.model_name = 'different'
                elif change == 'model_revision': run.model_revision = 'b'*40
                elif change in {'fingerprint', 'normalization', 'prefix'}:
                    key, value = {'fingerprint': ('model_files_sha256', 'different'),
                                  'normalization': ('normalize_embeddings', False),
                                  'prefix': ('prefix', 'query: ')}[change]
                    run.configuration = {**run.configuration, key: value}
                elif change == 'no_vectors':
                    for chunk in await db.scalars(select(DocumentChunk)):
                        chunk.embedding = None
            query = [1.0] + [0.0]*383
            fingerprint = publication.model_fingerprint(model)
            found = await search(query, 'a'*40, fingerprint, 5)
            assert found['status'] == expected
            expanded = await search(query, 'a'*40, fingerprint, 5,
                                    extra_vectors=[[0., 1.] + [0.]*382])
            assert expanded['status'] == expected
            assert {m['chunk_id']: m['cosine_distance'] for m in expanded['matches']} == pytest.approx(
                {m['chunk_id']: m['cosine_distance'] for m in found['matches']})
            if expected == 'matches_found':
                assert [m['chunk_index'] for m in found['matches']] == [0, 1]
                assert found['matches'][0]['cosine_distance'] == pytest.approx(0)
                assert found['matches'][1]['cosine_distance'] == pytest.approx(1)
                assert found['matches'][0]['source_spans']
                assert 'embedding' not in found['matches'][0]
                top = await search(query, 'a'*40, fingerprint, 1)
                assert len(top['matches']) == 1
                assert top['corpus_fingerprint'] == found['corpus_fingerprint']
                async with session_scope() as db:
                    active = await db.get(IngestionRun, UUID(found['matches'][0]['run_id']))
                    active.configuration = {**active.configuration, 'synthetic_calibration_revision': 2}
                changed = await search(query, 'a'*40, fingerprint, 5)
                assert changed['corpus_fingerprint'] != found['corpus_fingerprint']
                # Equal distances use IDs for a deterministic tie break.
                async with session_scope() as db:
                    for chunk in await db.scalars(select(DocumentChunk)):
                        chunk.embedding = query
                tied = await search(query, 'a'*40, fingerprint, 5)
                ids = [m['chunk_id'] for m in tied['matches']]
                assert ids == sorted(ids)
            else:
                assert not found['matches']
        finally:
            await close_database()
    run_async(scenario())
