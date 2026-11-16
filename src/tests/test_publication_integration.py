"""Publication transactions against the dedicated disposable PostgreSQL database only."""
from uuid import UUID
import pytest
from sqlalchemy import select, func, event, Text
from src.database import Base, session_scope, close_database, get_engine
from src.database.models import DocumentRevision, IngestionRun, DocumentChunk
from src.database.runtime import run_async
from src.ingestion import approved_publication as service
from src.ingestion import document_packages as p
from package_fixtures import ready, revised_package, set_metadata, batch_arguments

pytestmark = pytest.mark.integration


def test_atomic_publication_metadata_conflict_and_failure(empty_catalog_db, tmp_path, monkeypatch):
    package, _ = ready(tmp_path)
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
            result = await service.publish(tmp_path, package, 'a'*40, model_path=model)
            assert result['code'] == 'published'
            assert (await service.publish(tmp_path, package, 'a'*40, model_path=model))['status'] == 'skipped'
            record = p.metadata(package)
            record['owner'] = 'Changed synthetic owner'
            (package / 'metadata.json').write_bytes(p.encoded(record))
            second = package
            with pytest.raises(p.Rejected, match='metadata_conflict'):
                await service.publish(tmp_path, second, 'a'*40, model_path=model)
            def fail(*args):
                raise RuntimeError('synthetic embedding failure')
            monkeypatch.setattr(service, 'embed', fail)
            with pytest.raises(RuntimeError):
                await service.publish(tmp_path, second, 'a'*40, model_path=model, update_metadata=True, force=True)
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


@pytest.fixture
def local_model(empty_catalog_db, tmp_path, monkeypatch):
    """Real PostgreSQL transactions with deterministic synthetic embeddings."""
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights').write_bytes(b'synthetic')
    def embed(path, chunks, revision):
        for chunk in chunks:
            chunk['embedding'] = [1.0] + [0.0]*383
        return chunks
    monkeypatch.setattr(service, 'embed', embed)
    return model


async def database_state():
    """Read committed state in a fresh session, including vectors and all metadata."""
    async with session_scope() as db:
        state = {}
        for table in Base.metadata.sorted_tables:
            columns = [column.cast(Text).label(column.name) if column.name == 'embedding' else column
                       for column in table.columns]
            result = await db.execute(select(*columns).order_by(*table.primary_key.columns))
            state[table.name] = [dict(row) for row in result.mappings()]
        return state


@pytest.mark.parametrize('failure_stage', ['after_metadata', 'metadata_only', 'chunk_insert'])
def test_transaction_failure_restores_all_committed_state(tmp_path, local_model, monkeypatch, failure_stage):
    package, _ = ready(tmp_path)
    target, _ = ready(tmp_path, name='related-policy.txt')

    async def scenario():
        try:
            await service.publish(tmp_path, target, 'a'*40, model_path=local_model)
            first = await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            before = await database_state()
            record = set_metadata(package, owner='Changed owner', applicability='Changed scope',
                                  access_groups=['restricted'],
                                  relations=[{'type': 'supplements', 'revision_id': p.metadata(target)["revision_id"]}])
            reached = []
            engine = get_engine().sync_engine

            async def fail_after_metadata(db, metadata, revision):
                await original_write(db, metadata, revision)
                await db.flush()
                changed = await db.get(DocumentRevision, UUID(p.metadata(package)["revision_id"]))
                assert changed.owner == record['owner']
                reached.append('after_metadata')
                raise RuntimeError('synthetic transaction failure')

            def fail_chunk_insert(connection, cursor, statement, parameters, context, executemany):
                if statement.lstrip().lower().startswith('insert into document_chunks'):
                    rows = connection.execute(select(IngestionRun.id, IngestionRun.is_active).where(
                        IngestionRun.revision_id == UUID(p.metadata(package)["revision_id"]))).all()
                    assert len(rows) == 2
                    assert dict(rows)[UUID(first['run_id'])] is False
                    assert sum(active for _, active in rows) == 1
                    reached.append('chunk_insert')
                    raise RuntimeError('synthetic transaction failure')

            original_write = service.write_metadata
            if failure_stage in ('after_metadata', 'metadata_only'):
                monkeypatch.setattr(service, 'write_metadata', fail_after_metadata)
            else:
                event.listen(engine, 'before_cursor_execute', fail_chunk_insert)
            try:
                with pytest.raises(RuntimeError, match='synthetic transaction failure'):
                    await service.publish(tmp_path, package, 'a'*40, model_path=local_model,
                                          update_metadata=True, force=failure_stage != 'metadata_only')
            finally:
                if failure_stage == 'chunk_insert':
                    event.remove(engine, 'before_cursor_execute', fail_chunk_insert)
            assert reached == ['after_metadata' if failure_stage == 'metadata_only' else failure_stage]
            # Includes metadata, rights, relations, active flags, row counts, vector values and timestamps.
            assert await database_state() == before
        finally:
            await close_database()
    run_async(scenario())


def test_source_path_update_is_explicit_metadata_change(tmp_path, local_model):
    package, _ = ready(tmp_path)
    async def scenario():
        try:
            await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            before = await database_state()
            record = set_metadata(package, source_path='renamed-policy.txt')
            with pytest.raises(p.Rejected, match='metadata_conflict_use_update_metadata'):
                await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            assert await database_state() == before
            async with session_scope() as db:
                update_start = await db.scalar(select(func.statement_timestamp()))
            await service.publish(tmp_path, package, 'a'*40, model_path=local_model, update_metadata=True)
            async with session_scope() as db:
                revision = await db.get(DocumentRevision, UUID(p.metadata(package)["revision_id"]))
                assert revision.source_path == record['source_path']
                assert revision.source_path_key == record['source_path'].casefold()
                original = before['document_revisions'][0]
                assert revision.created_at == original['created_at']
                assert update_start <= revision.updated_at <= await db.scalar(select(func.statement_timestamp()))
                assert await db.scalar(select(func.count()).select_from(IngestionRun)) == 1
        finally:
            await close_database()
    run_async(scenario())


@pytest.mark.parametrize('skip_intermediate', [False, True])
def test_current_revision_replacement_requires_explicit_supersedes(tmp_path, local_model, skip_intermediate):
    package, _ = ready(tmp_path)
    original = p.snapshot_files(package)
    previous_id = p.metadata(package)['revision_id']
    async def scenario():
        try:
            await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            before = await database_state()
            revised_package(tmp_path, package)
            if skip_intermediate:
                revised_package(tmp_path, package)
            current_id = p.metadata(package)['revision_id']
            declared = p.metadata(package)['relations']
            set_metadata(package, relations=[])
            with pytest.raises(p.Rejected, match='current_revision_requires_explicit_supersedes'):
                await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            assert await database_state() == before
            set_metadata(package, relations=declared)
            result = await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            assert len(result['missing_supersedes']) == int(skip_intermediate)
            assert (await service.publish(tmp_path, package, 'a'*40, model_path=local_model))['status'] == 'skipped'
            async with session_scope() as db:
                previous = await db.get(DocumentRevision, UUID(previous_id))
                current = await db.get(DocumentRevision, UUID(current_id))
                assert previous.is_catalog_current is False and previous.lifecycle_status == 'superseded'
                assert current.is_catalog_current is True and current.lifecycle_status == 'active'
                assert current.document_id == previous.document_id
                assert previous.created_at == before['document_revisions'][0]['created_at']
                active_revisions = set(await db.scalars(select(IngestionRun.revision_id).where(IngestionRun.is_active.is_(True))))
                assert active_revisions == {previous.id, current.id}
                assert await db.scalar(select(func.count()).select_from(DocumentRevision).where(DocumentRevision.is_catalog_current.is_(True))) == 1
            after = await database_state()
            for filename, data in original.items():
                (package / filename).write_bytes(data)
            with pytest.raises(p.Rejected, match='current_revision_requires_explicit_supersedes'):
                await service.publish(tmp_path, package, 'a'*40, model_path=local_model, update_metadata=True)
            assert await database_state() == after
        finally:
            await close_database()
    run_async(scenario())


def test_latest_revision_bootstraps_empty_database(tmp_path, local_model):
    from src.database.models import DocumentRelation
    package, _ = ready(tmp_path)
    revised_package(tmp_path, package)
    revised_package(tmp_path, package)
    declared = sorted(r['revision_id'] for r in p.metadata(package)['relations'])
    async def scenario():
        try:
            preview = await service.publish(tmp_path, package, 'a'*40, model_path=local_model, dry_run=True)
            assert preview['missing_supersedes'] == declared
            result = await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            assert result['missing_supersedes'] == declared
            async with session_scope() as db:
                assert await db.scalar(select(func.count()).select_from(DocumentRevision)) == 1
                assert await db.scalar(select(func.count()).select_from(DocumentRelation)) == 0
                run = await db.get(IngestionRun, UUID(result['run_id']))
                assert run.configuration['declared_supersedes'] == declared
                assert run.configuration['missing_supersedes'] == declared
            assert (await service.publish(tmp_path, package, 'a'*40, model_path=local_model))['status'] == 'skipped'
        finally:
            await close_database()
    run_async(scenario())


@pytest.mark.parametrize('conflict', ['cross_document_supersedes', 'source_path_collision'])
def test_document_conflicts_leave_database_unchanged(tmp_path, local_model, conflict):
    package, _ = ready(tmp_path)
    other, _ = ready(tmp_path, name='other-policy.txt')
    if conflict == 'cross_document_supersedes':
        set_metadata(other, relations=[{'type': 'supersedes', 'revision_id': p.metadata(package)["revision_id"]}])
        code = 'supersedes_document_mismatch'
    else:
        code = 'duplicate_source_package'
    async def scenario():
        try:
            await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            before = await database_state()
            if conflict == 'source_path_collision':
                set_metadata(other, source_path=p.metadata(package)['source_path'])
            with pytest.raises(p.Rejected, match=code):
                await service.publish(tmp_path, other, 'a'*40, model_path=local_model, update_metadata=True)
            assert await database_state() == before
        finally:
            await close_database()
    run_async(scenario())


def test_batch_dry_run_does_not_create_relation_targets(tmp_path, local_model, capsys):
    import json
    from src.cli import publish_documents
    target, _ = ready(tmp_path, name='target.txt')
    dependent, _ = ready(tmp_path, name='dependent.txt')
    set_metadata(dependent, relations=[{'type': 'supplements', 'revision_id': p.metadata(target)["revision_id"]}])
    async def scenario():
        try:
            before = await database_state()
            args = batch_arguments([dependent, target], model_path=local_model, dry_run=True)
            assert await publish_documents.run(tmp_path, args) == 1
            results = json.loads(capsys.readouterr().out)['results']
            assert [(r['revision_id'], r['code']) for r in results] == [
                (p.metadata(target)["revision_id"], 'dry_run'), (p.metadata(dependent)["revision_id"], 'relation_target_missing')]
            assert await database_state() == before
        finally:
            await close_database()
    run_async(scenario())


def test_batch_rejection_preserves_independent_success(tmp_path, local_model, capsys):
    import json
    from uuid import uuid4
    from src.cli import publish_documents
    invalid, _ = ready(tmp_path, name='missing-target.txt')
    independent, _ = ready(tmp_path, name='independent.txt')
    set_metadata(invalid, relations=[{'type': 'supplements', 'revision_id': str(uuid4())}])
    async def scenario():
        try:
            args = batch_arguments([invalid, independent], model_path=local_model)
            assert await publish_documents.run(tmp_path, args) == 1
            results = json.loads(capsys.readouterr().out)['results']
            assert results[0]['code'] == 'relation_target_missing'
            assert results[1]['code'] == 'published'
            async with session_scope() as db:
                assert await db.get(DocumentRevision, UUID(p.metadata(invalid)["revision_id"])) is None
                assert await db.get(DocumentRevision, UUID(p.metadata(independent)["revision_id"])) is not None
                assert await db.scalar(select(func.count()).select_from(IngestionRun)) == 1
                assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 2
        finally:
            await close_database()
    run_async(scenario())


def test_metadata_only_and_incremental_publication_preserve_history(tmp_path, local_model, monkeypatch):
    from unittest.mock import Mock
    package, _ = ready(tmp_path)
    encoder = Mock(wraps=service.embed)
    monkeypatch.setattr(service, 'embed', encoder)

    async def scenario():
        try:
            first = await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            before = await database_state()
            set_metadata(package, owner='New owner', access_groups=['public', 'staff'])
            encoder.reset_mock()
            metadata = await service.publish(tmp_path, package, 'a'*40, model_path=local_model, update_metadata=True)
            assert metadata['code'] == 'metadata_updated' and metadata['run_id'] == first['run_id']
            encoder.assert_not_called()
            after_metadata = await database_state()
            assert after_metadata['ingestion_runs'] == before['ingestion_runs']
            assert after_metadata['document_chunks'] == before['document_chunks']
            assert (await service.publish(tmp_path, package, 'a'*40, model_path=local_model))['code'] == 'unchanged_publication'

            draft = p.decode((package / 'chunks.json').read_bytes())
            draft['chunks'][1]['content'] = 'Corrected synthetic paragraph.'
            (package / 'chunks.json').write_bytes(p.encoded(draft))
            second = await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            assert second['reused_chunk_count'] == second['encoded_chunk_count'] == 1
            assert second['encoded_unique_text_count'] == 1
            assert [c['content'] for c in encoder.call_args.args[1]] == ['Corrected synthetic paragraph.']
            after = await database_state()
            assert len(after['document_chunks']) == 4
            assert [r for r in after['document_chunks'] if str(r['run_id']) == first['run_id']] == before['document_chunks']
            assert sum(r['is_active'] for r in after['ingestion_runs']) == 1
            assert (await service.publish(tmp_path, package, 'a'*40, model_path=local_model))['code'] == 'identical_active_run'
        finally:
            await close_database()
    run_async(scenario())


def test_http_retrieval_ignores_inactive_and_superseded_publications(tmp_path, local_model, monkeypatch):
    """Real endpoint -> ApiService -> RetrievalService -> PostgreSQL; fake encoder only."""
    from types import SimpleNamespace
    import httpx
    import numpy as np
    from src.api.app import create_app
    from src.api.config import Settings
    from src.api.service import ApiService
    from src.retrieval import RetrievalService

    class Tokenizer:
        model_max_length = 512

        def __call__(self, text, **kwargs):
            return {'input_ids': [1, 2, 3]}

    fake_model = SimpleNamespace(max_seq_length=512, tokenizer=Tokenizer(),
                                 encode=lambda texts, **kwargs: np.array([[1.] + [0.] * 383 for _ in texts]))
    package, _ = ready(tmp_path)
    set_metadata(package, access_groups=['public'])

    async def scenario():
        try:
            first = await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            second = await service.publish(tmp_path, package, 'a'*40, model_path=local_model, force=True)
            revised_package(tmp_path, package)
            set_metadata(package, access_groups=['public'])
            third = await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            assert third['reused_chunk_count'] == 2 and third['encoded_chunk_count'] == 0
            settings = Settings(local_model, 'a'*40, relevance_filter_enabled=False)
            retrieval = RetrievalService(model_path=local_model, model_revision='a'*40,
                                          relevance_filter_enabled=False)
            monkeypatch.setattr(retrieval, '_load', lambda: (fake_model, service.model_fingerprint(local_model)))
            app = create_app(settings, service_factory=lambda config: ApiService(config, retrieval=retrieval))
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    response = await client.post('/api/v1/retrieve', json={'text': 'Synthetic policy', 'top_k': 20})
                    assert response.status_code == 200
                    data = response.json()
                    assert data['status'] == 'matches_found'
                    assert len(data['matches']) == 2
                    assert {m['run_id'] for m in data['matches']} == {third['run_id']}
                    assert not {first['run_id'], second['run_id']} & {m['run_id'] for m in data['matches']}
                    assert len({m['chunk_id'] for m in data['matches']}) == 2
                    assert 'candidate_order' not in data
        finally:
            await close_database()
    run_async(scenario())
