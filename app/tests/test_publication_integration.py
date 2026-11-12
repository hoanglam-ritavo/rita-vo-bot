"""Publication transactions against the dedicated disposable PostgreSQL database only."""
from uuid import UUID
import pytest
from sqlalchemy import select, func, event, Text
from app.database import Base, session_scope, close_database, get_engine
from app.database.models import DocumentRevision, IngestionRun, DocumentChunk
from app.database.runtime import run_async
from app.ingestion import approved_publication as service
from app.ingestion import document_packages as p
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


@pytest.mark.parametrize('failure_stage', ['after_metadata', 'chunk_insert'])
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
                                  relations=[{'type': 'supplements', 'revision_id': target.name}])
            reached = []
            engine = get_engine().sync_engine

            async def fail_after_metadata(db, metadata, revision):
                await original_write(db, metadata, revision)
                await db.flush()
                changed = await db.get(DocumentRevision, UUID(package.name))
                assert changed.owner == record['owner']
                reached.append('after_metadata')
                raise RuntimeError('synthetic transaction failure')

            def fail_chunk_insert(connection, cursor, statement, parameters, context, executemany):
                if statement.lstrip().lower().startswith('insert into document_chunks'):
                    rows = connection.execute(select(IngestionRun.id, IngestionRun.is_active).where(
                        IngestionRun.revision_id == UUID(package.name))).all()
                    assert len(rows) == 2
                    assert dict(rows)[UUID(first['run_id'])] is False
                    assert sum(active for _, active in rows) == 1
                    reached.append('chunk_insert')
                    raise RuntimeError('synthetic transaction failure')

            original_write = service.write_metadata
            if failure_stage == 'after_metadata':
                monkeypatch.setattr(service, 'write_metadata', fail_after_metadata)
            else:
                event.listen(engine, 'before_cursor_execute', fail_chunk_insert)
            try:
                with pytest.raises(RuntimeError, match='synthetic transaction failure'):
                    await service.publish(tmp_path, package, 'a'*40, model_path=local_model, update_metadata=True)
            finally:
                if failure_stage == 'chunk_insert':
                    event.remove(engine, 'before_cursor_execute', fail_chunk_insert)
            assert reached == [failure_stage]
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
                revision = await db.get(DocumentRevision, UUID(package.name))
                assert revision.source_path == record['source_path']
                assert revision.source_path_key == record['source_path'].casefold()
                original = before['document_revisions'][0]
                assert revision.created_at == original['created_at']
                assert update_start <= revision.updated_at <= await db.scalar(select(func.statement_timestamp()))
                assert await db.scalar(select(func.count()).select_from(IngestionRun)) == 2
        finally:
            await close_database()
    run_async(scenario())


def test_current_revision_replacement_requires_explicit_supersedes(tmp_path, local_model):
    package, _ = ready(tmp_path)
    replacement = revised_package(tmp_path, package)
    async def scenario():
        try:
            await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            before = await database_state()
            with pytest.raises(p.Rejected, match='current_revision_requires_explicit_supersedes'):
                await service.publish(tmp_path, replacement, 'a'*40, model_path=local_model)
            assert await database_state() == before
            set_metadata(replacement, relations=[{'type': 'supersedes', 'revision_id': package.name}])
            async with session_scope() as db:
                update_start = await db.scalar(select(func.statement_timestamp()))
            await service.publish(tmp_path, replacement, 'a'*40, model_path=local_model)
            async with session_scope() as db:
                previous = await db.get(DocumentRevision, UUID(package.name))
                current = await db.get(DocumentRevision, UUID(replacement.name))
                assert previous.is_catalog_current is False and previous.lifecycle_status == 'superseded'
                assert current.is_catalog_current is True and current.lifecycle_status == 'active'
                assert current.document_id == previous.document_id
                assert previous.created_at == before['document_revisions'][0]['created_at']
                assert update_start <= previous.updated_at <= await db.scalar(select(func.statement_timestamp()))
                active_revisions = set(await db.scalars(select(IngestionRun.revision_id).where(IngestionRun.is_active.is_(True))))
                assert active_revisions == {previous.id, current.id}
                assert await db.scalar(select(func.count()).select_from(DocumentRevision).where(DocumentRevision.is_catalog_current.is_(True))) == 1
            # Updating a revision does not change the parent document timestamp.
            assert (await database_state())['documents'] == before['documents']
        finally:
            await close_database()
    run_async(scenario())


@pytest.mark.parametrize('conflict', ['cross_document_supersedes', 'source_path_collision'])
def test_document_conflicts_leave_database_unchanged(tmp_path, local_model, conflict):
    package, _ = ready(tmp_path)
    other, _ = ready(tmp_path, name='other-policy.txt')
    if conflict == 'cross_document_supersedes':
        set_metadata(other, relations=[{'type': 'supersedes', 'revision_id': package.name}])
        code = 'supersedes_document_mismatch'
    else:
        set_metadata(other, source_path=p.metadata(package)['source_path'])
        code = 'current_revision_requires_explicit_supersedes'
    async def scenario():
        try:
            await service.publish(tmp_path, package, 'a'*40, model_path=local_model)
            before = await database_state()
            with pytest.raises(p.Rejected, match=code):
                await service.publish(tmp_path, other, 'a'*40, model_path=local_model, update_metadata=True)
            assert await database_state() == before
        finally:
            await close_database()
    run_async(scenario())


def test_batch_dry_run_does_not_create_relation_targets(tmp_path, local_model, capsys):
    import json
    from app.cli import publish_documents
    target, _ = ready(tmp_path, name='target.txt')
    dependent, _ = ready(tmp_path, name='dependent.txt')
    set_metadata(dependent, relations=[{'type': 'supplements', 'revision_id': target.name}])
    async def scenario():
        try:
            before = await database_state()
            args = batch_arguments([dependent, target], model_path=local_model, dry_run=True)
            assert await publish_documents.run(tmp_path, args) == 1
            results = json.loads(capsys.readouterr().out)['results']
            assert [(r['revision_id'], r['code']) for r in results] == [
                (target.name, 'dry_run'), (dependent.name, 'relation_target_missing')]
            assert await database_state() == before
        finally:
            await close_database()
    run_async(scenario())


def test_batch_rejection_preserves_independent_success(tmp_path, local_model, capsys):
    import json
    from uuid import uuid4
    from app.cli import publish_documents
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
                assert await db.get(DocumentRevision, UUID(invalid.name)) is None
                assert await db.get(DocumentRevision, UUID(independent.name)) is not None
                assert await db.scalar(select(func.count()).select_from(IngestionRun)) == 1
                assert await db.scalar(select(func.count()).select_from(DocumentChunk)) == 2
        finally:
            await close_database()
    run_async(scenario())
