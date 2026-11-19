"""The manifest CLI and its offline and per-wave publication gates."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.cli import publish_corpus, publish_documents
from src.database.runtime import run_async
from src.ingestion import approved_publication, corpus_manifest, corpus_publication, document_packages as packages
from package_fixtures import ready, set_metadata


REVISION = 'a' * 40


def eligible(root, filename, **metadata):
    package, _ = ready(root, name=filename)
    set_metadata(package, indexing_decision='include', lifecycle_status='active',
                 applicability='Synthetic scope', access_groups=['public'], **metadata)
    return package


def stub_tokenizer(monkeypatch):
    class Tokenizer:
        model_max_length = 512

        def __call__(self, *_args, **_kwargs):
            return {'input_ids': [1, 2, 3]}

    monkeypatch.setattr(corpus_publication.embeddings, 'load_model',
                        lambda _: SimpleNamespace(max_seq_length=512, tokenizer=Tokenizer()))


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(publish_corpus, 'REPOSITORY_ROOT', tmp_path)
    model_path = tmp_path / 'model'
    model_path.mkdir()
    (model_path / 'model.bin').write_bytes(b'synthetic E5 model')
    stub_tokenizer(monkeypatch)
    target = eligible(tmp_path, 'target.txt')
    dependent = eligible(tmp_path, 'dependent.txt', relations=[
        {'type': 'supplements', 'revision_id': packages.metadata(target)['revision_id']}])
    independent = eligible(tmp_path, 'independent.txt')
    return SimpleNamespace(root=tmp_path, target=target, dependent=dependent, independent=independent,
                           flags=['--model-path', str(model_path), '--model-revision', REVISION],
                           model_path=model_path)


def test_freeze_and_plan_preserve_legacy_selection(corpus, capsys, monkeypatch):
    assert publish_corpus.main(['freeze', '--all']) == 0
    frozen = json.loads(capsys.readouterr().out)
    assert (frozen['package_count'], frozen['chunk_count']) == (3, 6)
    assert publish_corpus.main(['freeze', '--all']) == 1
    assert json.loads(capsys.readouterr().out)['code'] == 'manifest_exists_use_replace'
    assert publish_corpus.main(['freeze', '--all', '--replace']) == 0
    assert json.loads(capsys.readouterr().out)['manifest_sha256'] == frozen['manifest_sha256']

    apply = AsyncMock(side_effect=AssertionError('offline plan accessed apply'))
    monkeypatch.setattr(corpus_publication, 'apply', apply)
    assert publish_corpus.main(['plan', *corpus.flags]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['waves'] == [sorted([corpus.target.name, corpus.independent.name]), [corpus.dependent.name]]
    assert plan['status'] == 'planned' and plan['database_checks'] == 'not_run'
    apply.assert_not_called()

    monkeypatch.setattr(publish_documents, 'REPOSITORY_ROOT', corpus.root)
    publish = AsyncMock(return_value={'status': 'processed', 'code': 'dry_run'})
    monkeypatch.setattr(approved_publication, 'publish', publish)
    for selection, expected in ((['--all'], 3), (['--package', str(corpus.target)], 1)):
        assert publish_documents.main([*selection, *corpus.flags, '--dry-run', '--json']) == 0
        assert json.loads(capsys.readouterr().out)['counts']['processed'] == expected


@pytest.mark.parametrize(('change', 'code'), [
    ('bytes', 'manifest_package_mismatch'), ('missing', 'incomplete_package'),
    ('duplicate', 'duplicate_manifest_entry'), ('metadata', 'publication_metadata_blocked'),
    ('token_limit', 'chunk_exceeds_model_limit'), ('cycle', 'cyclic_batch_relations'),
])
def test_apply_blocks_invalid_inputs_before_database(corpus, capsys, monkeypatch, change, code):
    if change == 'cycle':
        set_metadata(corpus.target, relations=[
            {'type': 'supplements', 'revision_id': packages.metadata(corpus.dependent)['revision_id']}])
    assert publish_corpus.main(['freeze', '--all']) == 0
    capsys.readouterr()
    if change == 'bytes':
        with (corpus.target / 'chunks.json').open('ab') as stream:
            stream.write(b' ')
    elif change == 'missing':
        (corpus.target / 'chunks.json').unlink()
    elif change == 'duplicate':
        manifest = corpus_manifest.manifest_path(corpus.root)
        data = packages.decode(manifest.read_bytes())
        data['packages'].append(data['packages'][0])
        manifest.write_bytes(packages.encoded(data))
    elif change == 'metadata':
        monkeypatch.setattr(corpus_publication, 'metadata_blockers', lambda _: ['effective_date_not_started'])
    elif change == 'token_limit':
        prepare = corpus_publication.embeddings.prepare_inputs

        def overlength(model, chunks):
            texts, limit, checks = prepare(model, chunks)
            checks[0]['status'] = 'exceeded'
            return texts, limit, checks

        monkeypatch.setattr(corpus_publication.embeddings, 'prepare_inputs', overlength)
    apply = AsyncMock()
    monkeypatch.setattr(corpus_publication, 'apply', apply)
    assert publish_corpus.main(['apply', *corpus.flags]) == 1
    assert json.loads(capsys.readouterr().out)['code'] == code
    apply.assert_not_called()


def test_missing_required_external_revision_blocks_all_writes(corpus, capsys, monkeypatch):
    external_id = str(uuid4())
    set_metadata(corpus.dependent, relations=[{'type': 'supplements', 'revision_id': external_id}])
    assert publish_corpus.main(['freeze', '--all']) == 0
    capsys.readouterr()
    missing = AsyncMock(return_value=[external_id])
    publish = AsyncMock()
    monkeypatch.setattr(corpus_publication, 'missing_external_revisions', missing)
    monkeypatch.setattr(approved_publication, 'publish', publish)
    assert publish_corpus.main(['apply', *corpus.flags]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['missing_external_revisions'] == [external_id]
    assert len(report['unattempted']) == 3
    missing.assert_awaited_once_with([external_id])
    publish.assert_not_called()


def test_apply_rejects_changed_expected_manifest_before_model_load(corpus, capsys, monkeypatch):
    assert publish_corpus.main(['freeze', '--all']) == 0
    capsys.readouterr()
    load_model = Mock()
    monkeypatch.setattr(corpus_publication.embeddings, 'load_model', load_model)
    assert publish_corpus.main(['apply', *corpus.flags, '--expected-manifest-sha256', '0' * 64]) == 1
    assert json.loads(capsys.readouterr().out)['code'] == 'manifest_changed'
    load_model.assert_not_called()


@pytest.mark.parametrize('stop', ['preview', 'uncertain', None])
def test_apply_orders_waves_and_reports_partial_results(corpus, capsys, monkeypatch, stop):
    first_wave = sorted([corpus.target, corpus.independent], key=lambda path: path.name)
    attempt_id = str(uuid4())
    calls = []

    async def publish(root, path, revision, *, dry_run, **kwargs):
        calls.append((path, dry_run))
        assert kwargs['expected_package_sha256']
        assert kwargs['expected_model_files_sha256']
        if stop == 'preview' and path == first_wave[1] and dry_run:
            raise packages.Rejected('test_preview_conflict')
        if stop == 'uncertain' and path == first_wave[1] and not dry_run:
            raise approved_publication.attempts.AttemptUncertain(attempt_id)
        return {'status': 'processed', 'code': 'dry_run' if dry_run else 'published'}

    monkeypatch.setattr(approved_publication, 'publish', publish)
    assert publish_corpus.main(['freeze', '--all']) == 0
    capsys.readouterr()
    assert publish_corpus.main(['apply', *corpus.flags]) == {None: 0, 'preview': 1, 'uncertain': 2}[stop]
    report = json.loads(capsys.readouterr().out)
    expected = [(path, True) for path in first_wave]
    if stop != 'preview':
        expected += [(path, False) for path in first_wave]
    if stop is None:
        expected += [(corpus.dependent, True), (corpus.dependent, False)]
    assert calls == expected
    assert len(report['unattempted']) == {None: 0, 'preview': 3, 'uncertain': 1}[stop]
    expected_published = {None: 3, 'preview': 0, 'uncertain': 1}[stop]
    assert sum(result['code'] == 'published' for result in report['results']) == expected_published
    if stop == 'uncertain':
        assert report['code'] == 'publication_outcome_unknown'
        assert report['results'][-1]['attempt_id'] == attempt_id


@pytest.mark.parametrize(('expected', 'code'), [
    ('expected_package_sha256', 'package_changed'), ('expected_model_files_sha256', 'model_changed'),
])
def test_locked_inputs_rechecked_before_starting_attempt(corpus, monkeypatch, expected, code):
    start = AsyncMock()
    monkeypatch.setattr(approved_publication.attempts, 'start', start)
    with pytest.raises(packages.Rejected, match=code):
        run_async(approved_publication.publish(
            corpus.root, corpus.target, REVISION, model_path=corpus.model_path, **{expected: '0' * 64}))
    start.assert_not_called()


def test_freeze_subset_and_reject_manifest_directory(corpus, capsys):
    assert publish_corpus.main(['freeze', '--package', str(corpus.target)]) == 0
    entries, _ = corpus_manifest.read(corpus.root)
    assert [entry['document_id'] for entry in entries] == [corpus.target.name]
    assert len(packages.package_paths(corpus.root)) == 3
    capsys.readouterr()
    path = corpus_manifest.manifest_path(corpus.root)
    path.unlink()
    path.mkdir()
    with pytest.raises(packages.Rejected, match='unexpected_package_entry'):
        packages.package_paths(corpus.root)


@pytest.mark.integration
def test_apply_to_disposable_database_and_skip_on_repeat(corpus, empty_catalog_db, monkeypatch, capsys):
    from sqlalchemy import select
    from src.database import close_database, session_scope
    from src.database.models import DocumentChunk, IngestionRun

    def embed(_path, chunks, _revision):
        for chunk in chunks:
            chunk['embedding'] = [1.0] + [0.0] * 383
        return chunks

    async def stored():
        try:
            async with session_scope() as db:
                rows = (await db.execute(select(IngestionRun.id, DocumentChunk.id, DocumentChunk.embedding)
                                        .join(DocumentChunk, DocumentChunk.run_id == IngestionRun.id)
                                        .where(IngestionRun.is_active)
                                        .order_by(IngestionRun.id, DocumentChunk.chunk_index))).all()
                assert all(len(vector) == 384 for _, _, vector in rows)
                return [(run_id, chunk_id) for run_id, chunk_id, _ in rows]
        finally:
            await close_database()

    monkeypatch.setattr(approved_publication, 'embed', embed)
    assert publish_corpus.main(['freeze', '--all']) == 0
    capsys.readouterr()
    assert publish_corpus.main(['apply', *corpus.flags]) == 0
    first = json.loads(capsys.readouterr().out)
    applied = [result for result in first['results'] if result['phase'] == 'publish']
    assert [result['code'] for result in applied] == ['published'] * 3
    assert applied[-1]['document_id'] == corpus.dependent.name
    initial = run_async(stored())
    assert len(initial) == 6 and len({run_id for run_id, _ in initial}) == 3

    assert publish_corpus.main(['apply', *corpus.flags]) == 0
    repeat = json.loads(capsys.readouterr().out)
    applied = [result for result in repeat['results'] if result['phase'] == 'publish']
    assert [result['status'] for result in applied] == ['skipped'] * 3
    assert run_async(stored()) == initial
