"""Synthetic ingestion tests: no model downloads or database connections."""
from contextlib import asynccontextmanager
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from ingestion_helpers import setup_source, options, chunk

from app.cli import ingest
from app.database.runtime import run_async
from app.ingestion import publish as producer


@pytest.mark.parametrize('change', ['hold', 'missing', 'changed', 'unknown', 'expired'])
def test_publish_source_rejection(tmp_path, change):
    record, source = setup_source(tmp_path)
    revision = UUID(record['revision_id'])
    if change == 'missing':
        source.unlink()
    elif change == 'changed':
        source.write_bytes(b'changed')
    elif change == 'unknown':
        revision = uuid4()
    else:
        if change == 'hold':
            record['indexing_decision'] = 'hold'
        else:
            record['effective_to'] = '2000-01-01'
        (tmp_path / 'indexing_workspace/catalog/documents.json').write_text(
            json.dumps({'schema_version': 1, 'documents': [record]}), encoding='utf-8')
    with pytest.raises(producer.PublishRejected):
        producer.selected_record(tmp_path, revision)


@pytest.mark.parametrize('vector', [[], [0.0] * 383, [float('nan')] * 384, [float('inf')] * 384, [True] * 384])
def test_invalid_embeddings(vector):
    data = chunk()
    data['embedding'] = vector
    with pytest.raises(RuntimeError):
        producer.validate_chunks([data])


def test_valid_chunks_and_empty_result():
    producer.validate_chunks([chunk()])
    with pytest.raises(producer.PublishRejected):
        producer.validate_chunks([])


@pytest.mark.parametrize('flag', ['--pages', '--ocr-region', '--skip-embeddings'])
def test_publish_rejects_review_options(flag):
    with pytest.raises(SystemExit) as result:
        ingest.main(['publish', '--revision-id', str(uuid4()), '--model-revision', 'a' * 40, flag])
    assert result.value.code == 2


@pytest.mark.parametrize('revision', ['main', 'latest', 'abc', 'z' * 40])
def test_requires_pinned_model(revision):
    with pytest.raises(SystemExit):
        ingest.main(['publish', '--revision-id', str(uuid4()), '--model-revision', revision])


def test_review_calls_shared_implementation(monkeypatch):
    pdf = pytest.importorskip('app.ingestion.pdf')
    from app.ingestion import embeddings, artifacts, review
    mocked = Mock(return_value=0)
    monkeypatch.setattr(review, 'run', mocked)
    assert ingest.main(['review', 'synthetic.pdf', '--skip-embeddings']) == 0
    mocked.assert_called_once()
    args = mocked.call_args.args[0]
    assert args.pdf == Path('synthetic.pdf')
    assert args.skip_embeddings is True


@pytest.mark.parametrize('error,exit_code', [(producer.PublishRejected('Rejected.'), 1), (RuntimeError('private'), 2)])
def test_publish_cli_closes_and_redacts(monkeypatch, capsys, error, exit_code):
    import app.database
    close = AsyncMock()
    monkeypatch.setattr(app.database, 'close_database', close)
    monkeypatch.setattr(producer, 'publish', AsyncMock(side_effect=error))
    assert run_async(ingest.run_publish(SimpleNamespace())) == exit_code
    close.assert_awaited_once()
    assert 'private' not in capsys.readouterr().err


def test_revision_metadata_and_permissions_must_match(tmp_path):
    record, _ = setup_source(tmp_path)
    revision = SimpleNamespace(**producer.revision_values(record))
    db = SimpleNamespace(get=AsyncMock(return_value=revision), scalars=AsyncMock(return_value=['staff']),
                         execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    run_async(producer.check_revision(db, record))
    revision.title = 'different'
    with pytest.raises(producer.PublishRejected):
        run_async(producer.check_revision(db, record))
    revision.title = record['title']
    db.scalars.return_value = ['other']
    with pytest.raises(producer.PublishRejected):
        run_async(producer.check_revision(db, record))


@pytest.mark.parametrize('force,changed', [(False, False), (True, False), (False, True)])
def test_skip_and_force(monkeypatch, tmp_path, force, changed):
    record, _ = setup_source(tmp_path)
    config = {'model_name': 'synthetic', 'model_revision': 'a' * 40}
    active = SimpleNamespace(id=uuid4(), source_sha256=record['sha256'], configuration=copy.deepcopy(config), chunk_count=1)
    if changed:
        config['model_revision'] = 'b' * 40
    db = SimpleNamespace(add=Mock())
    @asynccontextmanager
    async def scope():
        yield db
    monkeypatch.setattr(producer, 'session_scope', scope)
    monkeypatch.setattr(producer, 'lock', AsyncMock())
    monkeypatch.setattr(producer, 'check_revision', AsyncMock())
    monkeypatch.setattr(producer, 'active_run', AsyncMock(return_value=active))
    run_id, previous = run_async(producer.start_run(record, config, force))
    if not force and not changed:
        assert run_id is None and previous['status'] == 'skipped'
        db.add.assert_not_called()
    else:
        assert run_id and previous == active.id
        assert db.add.call_args.args[0].status == 'running'


@pytest.mark.parametrize('failure', ['embedding', 'snapshot', 'artifact', 'publication'])
def test_failed_run_never_reports_success(tmp_path, monkeypatch, failure):
    record, source = setup_source(tmp_path)
    rid = uuid4()
    monkeypatch.setattr(producer, 'processing_configuration', Mock(return_value=({}, None)))
    async def start(*args):
        if failure == 'snapshot':
            source.write_bytes(b'changed')
        return rid, None
    monkeypatch.setattr(producer, 'start_run', start)
    process = Mock(side_effect=RuntimeError('private') if failure in ('embedding', 'artifact') else None,
                   return_value=[chunk()])
    monkeypatch.setattr(producer, 'process_snapshot', process)
    finish = AsyncMock(side_effect=RuntimeError('database') if failure == 'publication' else None)
    failed = AsyncMock()
    monkeypatch.setattr(producer, 'finish_run', finish)
    monkeypatch.setattr(producer, 'fail_run', failed)
    with pytest.raises((RuntimeError, producer.PublishRejected)):
        run_async(producer.publish(tmp_path, options(record)))
    failed.assert_awaited_once()
    if failure != 'publication':
        finish.assert_not_awaited()


def test_native_processing_provenance_and_artifact_errors(tmp_path, monkeypatch):
    pdf = pytest.importorskip('app.ingestion.pdf')
    from app.ingestion import embeddings, artifacts, review
    source = tmp_path / 'source.pdf'
    with pdf.pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), 'Synthetic policy text')
        document.save(source)
    record = {'sha256': pdf.sha256_file(source), 'source_path': 'synthetic.pdf',
              'document_id': str(uuid4()), 'revision_id': str(uuid4())}
    args = options(record)
    monkeypatch.setattr(producer, 'version', lambda name: 'synthetic-version')
    config, tessdata = producer.processing_configuration(source, args)
    assert tessdata is None and config['language_sha256'] == {}
    def embed(chunks, **kw):
        assert kw['model_revision'] == 'a' * 40
        for item in chunks:
            item['embedding'] = chunk()['embedding']
        return chunks
    monkeypatch.setattr(embeddings, 'generate_embeddings', embed)
    result = producer.process_snapshot(tmp_path, source, record, args, config, tessdata, tmp_path)
    assert result[0]['source'] == 'synthetic.pdf'
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    assert manifest['configuration'] == config
    assert manifest['source_sha256'] == record['sha256']
    monkeypatch.setattr(artifacts, 'save_jsonl', Mock(side_effect=OSError('private')))
    with pytest.raises(OSError):
        producer.process_snapshot(tmp_path, source, record, args, config, tessdata, tmp_path)


def test_empty_page_blocks_embedding(tmp_path, monkeypatch):
    pdf = pytest.importorskip('app.ingestion.pdf')
    from app.ingestion import embeddings, artifacts, review
    def extract(*args, manifest, **kwargs):
        manifest['pages'] = [{'page': 2, 'empty': True}]
        return []
    monkeypatch.setattr(pdf, 'load_pdf', extract)
    embed = Mock(side_effect=AssertionError('Must not embed'))
    monkeypatch.setattr(embeddings, 'generate_embeddings', embed)
    record = {'sha256': 'a' * 64, 'source_path': 'synthetic.pdf', 'revision_id': str(uuid4())}
    with pytest.raises(producer.PublishRejected, match='2'):
        producer.process_snapshot(tmp_path, tmp_path / 'source.pdf', record, options(record), {}, None, tmp_path)
    embed.assert_not_called()


def test_embedding_revision_and_cardinality(tmp_path, monkeypatch):
    import sys
    pdf = pytest.importorskip('app.ingestion.pdf')
    from app.ingestion import embeddings, artifacts, review
    factory = Mock(return_value=SimpleNamespace(encode=Mock(return_value=[])))
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=factory))
    with pytest.raises(RuntimeError, match='count'):
        embeddings.generate_embeddings([chunk()], model_revision='a' * 40, cache_folder=tmp_path)
    factory.assert_called_once_with(embeddings.MODEL_NAME, revision='a' * 40, cache_folder=str(tmp_path))


@pytest.mark.parametrize('change', ['source', 'catalog'])
def test_final_preflight_rejects_changed_source_or_catalog(tmp_path, monkeypatch, change):
    record, source = setup_source(tmp_path)
    if change == 'source':
        source.write_bytes(b'changed')
    else:
        modified = dict(record, title='Changed title')
        (tmp_path / 'indexing_workspace/catalog/documents.json').write_text(
            json.dumps({'schema_version': 1, 'documents': [modified]}), encoding='utf-8')
    session = Mock(side_effect=AssertionError('Should reject before transaction'))
    monkeypatch.setattr(producer, 'session_scope', session)
    with pytest.raises(producer.PublishRejected):
        run_async(producer.finish_run(tmp_path, record, uuid4(), None, [chunk()]))
    session.assert_not_called()


def test_changed_active_run_rejected_before_writing(tmp_path, monkeypatch):
    record, _ = setup_source(tmp_path)
    db = SimpleNamespace(add_all=Mock(), flush=AsyncMock())
    @asynccontextmanager
    async def scope():
        yield db
    monkeypatch.setattr(producer, 'session_scope', scope)
    monkeypatch.setattr(producer, 'lock', AsyncMock())
    monkeypatch.setattr(producer, 'check_revision', AsyncMock())
    monkeypatch.setattr(producer, 'active_run', AsyncMock(return_value=SimpleNamespace(id=uuid4())))
    with pytest.raises(producer.PublishRejected, match='Another run'):
        run_async(producer.finish_run(tmp_path, record, uuid4(), None, [chunk()]))
    db.add_all.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.parametrize('status', ['running', 'succeeded'])
def test_failure_recording_preserves_committed_success(monkeypatch, status):
    run = SimpleNamespace(status=status, error_code=None, finished_at=None, is_active=status == 'succeeded')
    db = SimpleNamespace(get=AsyncMock(return_value=run))
    @asynccontextmanager
    async def scope():
        yield db
    monkeypatch.setattr(producer, 'session_scope', scope)
    monkeypatch.setattr(producer, 'lock', AsyncMock())
    run_async(producer.fail_run(uuid4(), 'processing_failed'))
    if status == 'running':
        assert run.status == 'failed' and run.finished_at is not None
    else:
        assert run.status == 'succeeded' and run.is_active and run.error_code is None
