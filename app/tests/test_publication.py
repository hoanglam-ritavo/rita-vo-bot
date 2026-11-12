"""Publication boundaries without network or live database."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import sys
import pytest
from app.database.runtime import run_async
from app.ingestion import approved_publication as service
from app.ingestion import document_packages as p
from package_fixtures import ready

@pytest.fixture
def publication(tmp_path, monkeypatch):
    db = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), add=Mock(), add_all=Mock())
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(service, 'session_scope', session)
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=(None, None, 'initial', {}, [])))
    monkeypatch.setattr(service, 'write_metadata', AsyncMock())
    def embed(path, chunks, revision):
        for c in chunks:
            c['embedding'] = [1.0] + [0.0] * 383
        return chunks
    monkeypatch.setattr(service, 'embed', Mock(side_effect=embed))
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights').write_bytes(b'synthetic')
    return db, model

def test_publish_and_dry_run(tmp_path, publication):
    db, model = publication
    package, _ = ready(tmp_path)
    result = run_async(service.publish(tmp_path, package, 'a'*40, model_path=model, dry_run=True))
    assert result['code'] == 'dry_run'
    service.embed.assert_not_called()
    service.write_metadata.assert_not_called()
    db.add.assert_not_called()
    result = run_async(service.publish(tmp_path, package, 'a'*40, model_path=model))
    assert result['code'] == 'published'
    chunks = list(db.add_all.call_args.args[0])
    assert chunks[0].content == 'Original policy.'
    assert not (p.workspace(tmp_path) / 'approved').exists()

@pytest.mark.parametrize('dry_run', [False, True])
def test_ineligible_rejected_before_model_or_database(tmp_path, monkeypatch, dry_run):
    path, _ = ready(tmp_path)
    record = p.metadata(path)
    record['indexing_decision'] = 'hold'
    (path / 'metadata.json').write_bytes(p.encoded(record))
    model, database = Mock(), Mock()
    monkeypatch.setattr(service, 'model_fingerprint', model)
    monkeypatch.setattr(service, 'session_scope', database)
    with pytest.raises(p.Rejected, match='source_ineligible'):
        run_async(service.publish(tmp_path, path, 'a'*40, model_path=tmp_path, dry_run=dry_run))
    model.assert_not_called()
    database.assert_not_called()


def test_concurrency_rejected_before_metadata(tmp_path, publication):
    _, model = publication
    package, _ = ready(tmp_path)
    service.snapshot.side_effect = [(None, None, 'before', {}, []), (None, None, 'after', {}, [])]
    with pytest.raises(p.Rejected, match='concurrent'):
        run_async(service.publish(tmp_path, package, 'a'*40, model_path=model))
    service.write_metadata.assert_not_called()

def test_embedding_failure_does_not_write_metadata(tmp_path, publication):
    _, model = publication
    package, _ = ready(tmp_path)
    service.embed.side_effect = RuntimeError('synthetic')
    with pytest.raises(RuntimeError):
        run_async(service.publish(tmp_path, package, 'a'*40, model_path=model))
    service.write_metadata.assert_not_called()

def test_local_model_and_token_limit(tmp_path, monkeypatch):
    model = SimpleNamespace(max_seq_length=4, tokenizer=Mock(return_value={'input_ids': [1]*5}),
                            encode=Mock(), get_sentence_embedding_dimension=lambda: 384)
    model.tokenizer.model_max_length = 8
    factory = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=factory))
    with pytest.raises(p.Rejected, match='exceeds_model_limit'):
        service.embed(tmp_path, [{'content': 'long'}], 'a'*40)
    assert factory.call_args.kwargs == {'local_files_only': True, 'trust_remote_code': False}
    model.encode.assert_not_called()

@pytest.mark.parametrize('vector', [[0.0], [float('nan')]*384, [True]*384])
def test_invalid_vectors(vector):
    with pytest.raises(p.Rejected):
        service.validate_vectors([{'chunk_index': 0, 'embedding': vector}])


def test_snapshot_conflicts_and_missing_relations():
    from uuid import uuid4, UUID
    from app.catalog.validation import new_record
    from app.catalog.importer import revision_values
    record = new_record('synthetic.txt', {'sha256': 'a'*64, 'size_bytes': 3, 'extension': '.txt'})
    revision = SimpleNamespace(**revision_values(record))
    revision.owner = 'different'
    empty = Mock()
    empty.all.return_value = []
    db = SimpleNamespace(get=AsyncMock(return_value=revision), scalars=AsyncMock(return_value=[]),
                         execute=AsyncMock(return_value=empty), scalar=AsyncMock(return_value=None))
    with pytest.raises(p.Rejected, match='metadata_conflict'):
        run_async(service.snapshot(db, record))
    run_async(service.snapshot(db, record, True))
    revision.sha256 = 'b'*64
    with pytest.raises(p.Rejected, match='identity_conflict'):
        run_async(service.snapshot(db, record, True))
    record['relations'] = [{'type': 'supplements', 'revision_id': str(uuid4())}]
    db.get.side_effect = [None, None]
    with pytest.raises(p.Rejected, match='target_missing'):
        run_async(service.snapshot(db, record))


def test_identical_run_and_force(tmp_path, publication):
    _, model = publication
    path, _ = ready(tmp_path)
    record, payload, files = p.load_package(tmp_path, path)
    config = service.configuration(payload, 'a'*40)
    config.update(model_files_sha256=service.model_fingerprint(model), package_sha256=p.files_hash(files),
                  metadata_sha256=p.sha(files['metadata.json']))
    active = SimpleNamespace(configuration=config, id='old-run', is_active=True)
    service.snapshot.return_value = (None, active, 'initial', {}, [])
    assert run_async(service.publish(tmp_path, path, 'a'*40, model_path=model))['status'] == 'skipped'
    service.embed.assert_not_called()
    assert run_async(service.publish(tmp_path, path, 'a'*40, model_path=model, force=True))['code'] == 'published'
    assert active.is_active is False
    db = publication[0]
    assert db.add.call_args.args[0].configuration == config
    assert not any('approval' in k for k in config)


@pytest.mark.parametrize('file', ['chunks.json', 'metadata.json', 'source.txt'])
def test_package_change_during_embedding(tmp_path, publication, file):
    _, model = publication
    path, _ = ready(tmp_path)
    original = service.embed.side_effect
    def changed(*args):
        result = original(*args)
        with (path / file).open('ab') as stream:
            stream.write(b' ')
        return result
    service.embed.side_effect = changed
    with pytest.raises(p.Rejected, match='package_changed'):
        run_async(service.publish(tmp_path, path, 'a'*40, model_path=model))
    service.write_metadata.assert_not_called()


@pytest.mark.parametrize('file', ['chunks.json', 'metadata.json'])
def test_content_or_metadata_change_does_not_skip(tmp_path, publication, file):
    _, model = publication
    path, _ = ready(tmp_path)
    record, payload, files = p.load_package(tmp_path, path)
    config = service.configuration(payload, 'a'*40)
    config.update(model_files_sha256=service.model_fingerprint(model), package_sha256=p.files_hash(files),
                  metadata_sha256=p.sha(files['metadata.json']))
    active = SimpleNamespace(configuration=config, id='old', is_active=True)
    service.snapshot.return_value = (None, active, 'initial', {}, [])
    value = p.decode((path / file).read_bytes())
    if file == 'metadata.json':
        value['owner'] = 'Changed owner'
    else:
        value['chunks'][0]['content'] = 'Corrected policy.'
    (path / file).write_bytes(p.encoded(value))
    assert run_async(service.publish(tmp_path, path, 'a'*40, model_path=model, update_metadata=True))['code'] == 'published'
    service.embed.assert_called_once()


def test_failure_preserves_previous_active_run(tmp_path, publication):
    _, model = publication
    path, _ = ready(tmp_path)
    active = SimpleNamespace(configuration={}, id='old', is_active=True)
    service.snapshot.return_value = (None, active, 'initial', {}, [])
    service.embed.side_effect = RuntimeError('synthetic embedding failure')
    with pytest.raises(RuntimeError):
        run_async(service.publish(tmp_path, path, 'a'*40, model_path=model))
    assert active.is_active is True
    service.write_metadata.assert_not_called()


def test_change_while_waiting_for_publication_lock(tmp_path, publication):
    _, model = publication
    path, _ = ready(tmp_path)
    calls = 0
    async def snapshot(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            with (path / 'chunks.json').open('ab') as stream:
                stream.write(b' ')
        return None, None, 'initial', {}, []
    service.snapshot.side_effect = snapshot
    with pytest.raises(p.Rejected, match='package_changed'):
        run_async(service.publish(tmp_path, path, 'a'*40, model_path=model))
    service.write_metadata.assert_not_called()


def stored_revision():
    """A query double for identity checks, without transaction assertions."""
    from app.catalog.validation import new_record
    from app.catalog.importer import revision_values
    record = new_record('original.txt', {'sha256': 'a'*64, 'size_bytes': 3, 'extension': '.txt'})
    revision = SimpleNamespace(**revision_values(record))
    empty = Mock()
    empty.all.return_value = []
    db = SimpleNamespace(get=AsyncMock(return_value=revision), scalars=AsyncMock(return_value=[]),
                         execute=AsyncMock(return_value=empty), scalar=AsyncMock(return_value=None))
    return record, db


def test_source_path_is_metadata_update_not_immutable_identity():
    record, db = stored_revision()
    record['source_path'] = 'renamed.txt'
    p.validate_metadata(record)
    with pytest.raises(p.Rejected, match='metadata_conflict_use_update_metadata'):
        run_async(service.snapshot(db, record))
    run_async(service.snapshot(db, record, update_metadata=True))


@pytest.mark.parametrize('field,value', [
    ('document_id', '00000000-0000-0000-0000-000000000001'),
    ('sha256', 'b'*64), ('size_bytes', 4), ('extension', '.md'),
])
def test_immutable_revision_fields_rejected_even_with_update_metadata(field, value):
    record, db = stored_revision()
    record[field] = value
    if field == 'extension':
        record['source_path'] = 'original.md'
    p.validate_metadata(record)
    with pytest.raises(p.Rejected, match='revision_identity_conflict'):
        run_async(service.snapshot(db, record, update_metadata=True))


def test_missing_supersedes_resolution_and_concurrency_token():
    from uuid import UUID, uuid4
    record, db = stored_revision()
    target = str(uuid4())
    record['relations'] = [{'type': 'supersedes', 'revision_id': target}]
    db.get.side_effect = [None, None]
    _, _, absent_token, resolved, missing = run_async(service.snapshot(db, record))
    assert missing == [target] and resolved['relations'] == []
    assert record['relations']  # Declared provenance is not discarded.
    other = SimpleNamespace(document_id=UUID(record['document_id']), sha256='b'*64)
    db.get.side_effect = [None, other]
    _, _, present_token, resolved, missing = run_async(service.snapshot(db, record))
    assert not missing and resolved['relations'] == record['relations']
    assert present_token != absent_token
    other.document_id = uuid4()
    db.get.side_effect = [None, other]
    with pytest.raises(p.Rejected, match='supersedes_document_mismatch'):
        run_async(service.snapshot(db, record))


def test_missing_ancestor_does_not_cause_metadata_conflict():
    from uuid import uuid4
    record, db = stored_revision()
    stored = db.get.return_value
    record['relations'] = [{'type': 'supersedes', 'revision_id': str(uuid4())}]
    db.get.side_effect = [stored, None]
    assert run_async(service.snapshot(db, record))[4]


def test_missing_supersedes_is_reported_and_persisted(tmp_path, publication):
    from uuid import uuid4
    from package_fixtures import set_metadata
    db, model = publication
    package, _ = ready(tmp_path)
    target = str(uuid4())
    record = set_metadata(package, relations=[{'type': 'supersedes', 'revision_id': target}])
    resolved = dict(record, relations=[])
    service.snapshot.return_value = (None, None, 'initial', resolved, [target])
    preview = run_async(service.publish(tmp_path, package, 'a'*40, model_path=model, dry_run=True))
    assert preview['missing_supersedes'] == [target]
    result = run_async(service.publish(tmp_path, package, 'a'*40, model_path=model))
    assert result['missing_supersedes'] == [target]
    service.write_metadata.assert_awaited_once_with(db, resolved, None)
    config = db.add.call_args.args[0].configuration
    assert config['declared_supersedes'] == [target]
    assert config['missing_supersedes'] == [target]
    active = SimpleNamespace(configuration=config, id='existing', is_active=True)
    service.snapshot.return_value = (None, active, 'initial', resolved, [target])
    result = run_async(service.publish(tmp_path, package, 'a'*40, model_path=model))
    assert result['status'] == 'skipped' and result['missing_supersedes'] == [target]


def test_supersedes_cycle_remains_rejected():
    from uuid import UUID, uuid4
    record, db = stored_revision()
    target = uuid4()
    record['relations'] = [{'type': 'supersedes', 'revision_id': str(target)}]
    db.get.side_effect = [None, SimpleNamespace(document_id=UUID(record['document_id']), sha256='b'*64)]
    empty, edges = Mock(), Mock()
    empty.all.return_value = []
    edges.all.return_value = [(target, UUID(record['revision_id']))]
    db.execute.side_effect = [empty, edges]
    with pytest.raises(p.Rejected, match='supersedes_cycle'):
        run_async(service.snapshot(db, record))
