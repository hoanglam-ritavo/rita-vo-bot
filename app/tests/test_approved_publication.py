"""Approved publication behavior with isolated database/model doubles."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
import sys

import pytest

from app.database.runtime import run_async
from app.ingestion import approved_publication as service
from app.ingestion import document_packages as p
from app.paths import workspace
from test_document_packages import approved, edit


def fake_embed(root, chunks, revision):
    for c in chunks:
        c['embedding'] = [1.0] + [0.0] * 383
    return chunks


@pytest.fixture
def publication(monkeypatch):
    monkeypatch.setattr(service, 'preflight_database', AsyncMock())
    monkeypatch.setattr(service, 'version', lambda name: 'test')
    monkeypatch.setattr(service, 'embed', Mock(side_effect=fake_embed))
    monkeypatch.setattr(service.lifecycle, 'start_run', AsyncMock(return_value=(uuid4(), None)))
    monkeypatch.setattr(service.lifecycle, 'activate_run', AsyncMock())
    monkeypatch.setattr(service.lifecycle, 'fail_run', AsyncMock())
    return service


def test_publish_uses_reviewed_content_and_artifacts(tmp_path, publication, monkeypatch):
    _, _, _, approval = approved(tmp_path)
    from app.ingestion import document_extractors, document_preparation
    monkeypatch.setattr(document_extractors, 'extract', Mock(side_effect=AssertionError('Parser called')))
    monkeypatch.setattr(document_preparation, 'initial_chunks', Mock(side_effect=AssertionError('Rechunked')))
    result = run_async(publication.publish(tmp_path, approval, 'a' * 40))
    assert result['code'] == 'published'
    chunks = publication.lifecycle.activate_run.call_args.args[-1]
    assert chunks[0]['content'] == 'Human corrected policy.'
    assert chunks[0]['source_location']['spans'][0]['location']['line_start'] == 1
    stored = next((workspace(tmp_path) / 'published_artifacts').glob('*/*/*/payload.json'))
    assert p.decode(stored.read_bytes())['chunks'][0]['content'] == 'Human corrected policy.'


def test_dry_run_no_model_or_run(tmp_path, publication):
    *_, approval = approved(tmp_path)
    assert run_async(publication.publish(tmp_path, approval, 'a' * 40, dry_run=True))['code'] == 'dry_run'
    publication.embed.assert_not_called()
    publication.lifecycle.start_run.assert_not_called()
    assert not (workspace(tmp_path) / 'published_artifacts').exists()


def test_skip_and_force(tmp_path, publication):
    *_, approval = approved(tmp_path)
    publication.lifecycle.start_run.return_value = (None, {'status': 'skipped', 'run_id': str(uuid4())})
    assert run_async(publication.publish(tmp_path, approval, 'a' * 40))['status'] == 'skipped'
    publication.embed.assert_not_called()
    publication.lifecycle.start_run.return_value = (uuid4(), None)
    run_async(publication.publish(tmp_path, approval, 'a' * 40, force=True))
    assert publication.lifecycle.start_run.call_args.args[-1] is True


@pytest.mark.parametrize('phase', ['embedding', 'artifact', 'activation'])
def test_failure_preserves_activation_boundary(tmp_path, publication, monkeypatch, phase):
    *_, approval = approved(tmp_path)
    if phase == 'embedding': publication.embed.side_effect = RuntimeError('failure')
    if phase == 'artifact': monkeypatch.setattr(p, 'write_package', Mock(side_effect=OSError('failure')))
    if phase == 'activation': publication.lifecycle.activate_run.side_effect = RuntimeError('failure')
    with pytest.raises((RuntimeError, OSError)):
        run_async(publication.publish(tmp_path, approval, 'a' * 40))
    publication.lifecycle.fail_run.assert_awaited_once()
    if phase != 'activation': publication.lifecycle.activate_run.assert_not_called()


def test_embedding_adapter_cannot_modify_evidence(tmp_path, publication):
    *_, approval = approved(tmp_path)
    def corrupt(root, chunks, revision):
        chunks[0]['content'] = 'Unauthorized rewrite'
        return fake_embed(root, chunks, revision)
    publication.embed.side_effect = corrupt
    with pytest.raises(p.Rejected, match='changed_content'):
        run_async(publication.publish(tmp_path, approval, 'a' * 40))
    publication.lifecycle.activate_run.assert_not_called()


def test_token_check_prevents_silent_truncation(tmp_path, monkeypatch):
    model = SimpleNamespace(max_seq_length=4, tokenizer=Mock(return_value={'input_ids': [1, 2, 3, 4, 5]}), encode=Mock())
    model.tokenizer.model_max_length = 8
    factory = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=factory))
    with pytest.raises(p.Rejected, match='exceeds_model_limit'):
        service.embed(tmp_path, [{'content': 'long chunk'}], 'a' * 40)
    model.encode.assert_not_called()
    model.tokenizer.assert_called_once_with('passage: long chunk', add_special_tokens=True, truncation=False)


@pytest.mark.parametrize('vector', [[0.0], [float('nan')] * 384, [True] * 384, [float('inf')] * 384])
def test_invalid_vectors(vector):
    with pytest.raises(p.Rejected, match='invalid_vectors'):
        service.validate_vectors([{'chunk_index': 0, 'embedding': vector}])


def test_approval_timestamps_do_not_change_content_key(tmp_path):
    _, _, review, first = approved(tmp_path)
    second = p.approve(tmp_path, review, 'Another reviewer')
    a = p.load_approval(tmp_path, first)[1]
    b = p.load_approval(tmp_path, second)[1]
    assert service.configuration(a, 'a' * 40) == service.configuration(b, 'a' * 40)
    edit(review, lambda d: d['chunks'][0].update(content='Changed again'))
    third = p.approve(tmp_path, review, 'Reviewer')
    c = p.load_approval(tmp_path, third)[1]
    assert service.configuration(a, 'a' * 40) != service.configuration(c, 'a' * 40)


def test_publish_batch_ambiguity_and_partial_failure(tmp_path, monkeypatch):
    from app.cli import publish_documents as cli
    import app.database
    _, _, review, first = approved(tmp_path)
    second = p.approve(tmp_path, review, 'Another reviewer')
    publish = AsyncMock()
    monkeypatch.setattr(service, 'publish', publish)
    monkeypatch.setattr(app.database, 'close_database', AsyncMock())
    args = SimpleNamespace(all=True, approved=None, model_revision='a' * 40, dry_run=False, force=False)
    assert run_async(cli.run(tmp_path, args)) == 1
    publish.assert_not_called()
    args.all, args.approved = False, [first]
    publish.return_value = {'status': 'processed', 'code': 'published'}
    assert run_async(cli.run(tmp_path, args)) == 0
    publish.assert_awaited_once()


def test_source_change_during_embedding_blocks_activation(tmp_path, publication):
    _, source, _, approval = approved(tmp_path)
    def drift(root, chunks, revision):
        source.write_text('modified')
        return fake_embed(root, chunks, revision)
    publication.embed.side_effect = drift
    with pytest.raises(p.Rejected, match='drift'):
        run_async(publication.publish(tmp_path, approval, 'a' * 40))
    publication.lifecycle.activate_run.assert_not_called()
