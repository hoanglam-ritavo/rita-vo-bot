"""Publication boundaries without network or live database."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import sys
import pytest
from app.database.runtime import run_async
from app.ingestion import approved_publication as service
from app.ingestion import document_packages as p
from test_document_packages import approved

@pytest.fixture
def publication(tmp_path, monkeypatch):
    db = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock(), add=Mock(), add_all=Mock())
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(service, 'session_scope', session)
    monkeypatch.setattr(service, 'snapshot', AsyncMock(return_value=(None, None, 'initial')))
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
    *_, approval = approved(tmp_path)
    result = run_async(service.publish(tmp_path, approval, 'a'*40, model_path=model, dry_run=True))
    assert result['code'] == 'dry_run'
    service.embed.assert_not_called()
    service.write_metadata.assert_not_called()
    db.add.assert_not_called()
    result = run_async(service.publish(tmp_path, approval, 'a'*40, model_path=model))
    assert result['code'] == 'published'
    chunks = list(db.add_all.call_args.args[0])
    assert chunks[0].content == 'Human corrected policy.'
    assert set(d.name for d in p.workspace(tmp_path).iterdir()) == {'review', 'approved'}

def test_concurrency_rejected_before_metadata(tmp_path, publication):
    _, model = publication
    *_, approval = approved(tmp_path)
    service.snapshot.side_effect = [(None, None, 'before'), (None, None, 'after')]
    with pytest.raises(p.Rejected, match='concurrent'):
        run_async(service.publish(tmp_path, approval, 'a'*40, model_path=model))
    service.write_metadata.assert_not_called()

def test_embedding_failure_does_not_write_metadata(tmp_path, publication):
    _, model = publication
    *_, approval = approved(tmp_path)
    service.embed.side_effect = RuntimeError('synthetic')
    with pytest.raises(RuntimeError):
        run_async(service.publish(tmp_path, approval, 'a'*40, model_path=model))
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


def test_batch_multiple_approvals_rejected(tmp_path, monkeypatch, capsys):
    from app.cli import publish_documents as cli
    import app.database
    _, _, review, first = approved(tmp_path)
    p.approve(tmp_path, review, 'Second reviewer')
    publish = AsyncMock()
    monkeypatch.setattr(service, 'publish', publish)
    monkeypatch.setattr(app.database, 'close_database', AsyncMock())
    args = SimpleNamespace(all=True, approved=None, model_revision='a'*40, model_path=tmp_path,
                           dry_run=False, force=False, update_metadata=False, json=True)
    assert run_async(cli.run(tmp_path, args)) == 1
    publish.assert_not_called()
    assert 'multiple_approvals_select_one' in capsys.readouterr().out
