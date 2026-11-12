"""Workspace routing and historical-package isolation, using synthetic data."""
import importlib.util
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import paths
from app.cli import prepare_documents, publish_documents
from app.database.runtime import run_async
from app.ingestion import approved_publication, document_packages as p
from package_fixtures import ready


def test_paths_import_is_repository_anchored_without_creating_workspace(tmp_path, monkeypatch):
    module_file = tmp_path / 'app' / 'paths.py'
    module_file.parent.mkdir()
    module_file.write_bytes(Path(paths.__file__).read_bytes())
    monkeypatch.chdir(tmp_path.parent)
    spec = importlib.util.spec_from_file_location('isolated_paths', module_file)
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    assert isolated.workspace() == tmp_path / 'app' / 'chunks'
    assert isolated.workspace(tmp_path / 'other') == tmp_path / 'other' / 'app' / 'chunks'
    assert paths.workspace() == paths.REPOSITORY_ROOT / 'app' / 'chunks'
    assert isolated.assets() == tmp_path / 'app' / 'assets'
    assert not (tmp_path / 'app' / 'chunks').exists()


def test_validation_uses_new_root(tmp_path, monkeypatch, capsys):
    path, _ = ready(tmp_path)
    assert path.is_relative_to(tmp_path / 'app/chunks')
    monkeypatch.setattr(prepare_documents, 'REPOSITORY_ROOT', tmp_path)
    assert prepare_documents.main(['validate', '--package', str(path), '--json']) == 0
    assert 'valid_package' in capsys.readouterr().out


def test_batch_preserves_historical_and_legacy_packages(tmp_path, monkeypatch, capsys):
    import app.database
    path, _ = ready(tmp_path)
    old = tmp_path / 'indexing_chunks'
    shutil.copytree(paths.workspace(tmp_path), old)
    legacy = paths.workspace(tmp_path) / 'approved/unchanged'
    legacy.mkdir(parents=True)
    (legacy / 'private').write_bytes(b'synthetic')
    before = {f.relative_to(old): f.read_bytes() for f in old.rglob('*') if f.is_file()}
    publish = AsyncMock(return_value={'status': 'processed', 'code': 'dry_run'})
    monkeypatch.setattr(approved_publication, 'publish', publish)
    monkeypatch.setattr(app.database, 'close_database', AsyncMock())
    args = SimpleNamespace(all=True, package=None, model_revision='a' * 40, model_path=tmp_path,
                           dry_run=True, force=False, update_metadata=False, json=True)
    assert run_async(publish_documents.run(tmp_path, args)) == 0
    assert publish.await_count == 1 and publish.await_args.args[1] == path
    capsys.readouterr()
    with pytest.raises(p.Rejected, match='package_outside_workspace'):
        p.load_package(tmp_path, old / path.relative_to(paths.workspace(tmp_path)))
    # Remove only this synthetic package, retaining legacy data in the workspace.
    shutil.rmtree(path.parent)
    publish.reset_mock()
    assert run_async(publish_documents.run(tmp_path, args)) == 1
    assert 'no_packages' in capsys.readouterr().out
    publish.assert_not_called()
    assert (legacy / 'private').read_bytes() == b'synthetic'
    assert before == {f.relative_to(old): f.read_bytes() for f in old.rglob('*') if f.is_file()}


def test_batch_reports_incomplete_packages(tmp_path, monkeypatch, capsys):
    import app.database
    path, _ = ready(tmp_path)
    (path / 'chunks.json').unlink()
    monkeypatch.setattr(app.database, 'close_database', AsyncMock())
    args = SimpleNamespace(all=True, package=None, model_revision='a'*40, model_path=tmp_path,
                           dry_run=True, force=False, update_metadata=False, json=True)
    assert run_async(publish_documents.run(tmp_path, args)) == 1
    assert 'incomplete_package' in capsys.readouterr().out


@pytest.fixture
def batch_publisher(monkeypatch):
    import app.database
    publish = AsyncMock(return_value={'status': 'processed', 'code': 'dry_run'})
    monkeypatch.setattr(approved_publication, 'publish', publish)
    monkeypatch.setattr(app.database, 'close_database', AsyncMock())
    return publish


def test_batch_orders_relation_target_before_dependent(tmp_path, batch_publisher, capsys):
    from package_fixtures import batch_arguments, set_metadata
    target, _ = ready(tmp_path, name='target.txt')
    dependent, _ = ready(tmp_path, name='dependent.txt')
    set_metadata(dependent, relations=[{'type': 'supplements', 'revision_id': target.name}])
    args = batch_arguments([dependent, target], dry_run=True)
    assert run_async(publish_documents.run(tmp_path, args)) == 0
    assert [call.args[1] for call in batch_publisher.await_args_list] == [target, dependent]
    capsys.readouterr()


def test_batch_cycle_rejected_but_independent_package_still_runs(tmp_path, batch_publisher, capsys):
    from package_fixtures import batch_arguments, set_metadata
    first, _ = ready(tmp_path, name='first.txt')
    second, _ = ready(tmp_path, name='second.txt')
    independent, _ = ready(tmp_path, name='independent.txt')
    set_metadata(first, relations=[{'type': 'supplements', 'revision_id': second.name}])
    set_metadata(second, relations=[{'type': 'supplements', 'revision_id': first.name}])
    assert run_async(publish_documents.run(tmp_path, batch_arguments([first, second, independent]))) == 1
    assert 'cyclic_batch_relations' in capsys.readouterr().out
    batch_publisher.assert_awaited_once()
    assert batch_publisher.await_args.args[1] == independent


def test_batch_duplicate_selection_is_not_published(tmp_path, batch_publisher, capsys):
    from package_fixtures import batch_arguments
    package, _ = ready(tmp_path)
    assert run_async(publish_documents.run(tmp_path, batch_arguments([package, package]))) == 1
    assert 'multiple_packages_select_one' in capsys.readouterr().out
    batch_publisher.assert_not_called()


def test_batch_continues_after_an_independent_publication_failure(tmp_path, batch_publisher, capsys):
    from package_fixtures import batch_arguments
    first, _ = ready(tmp_path, name='first.txt')
    second, _ = ready(tmp_path, name='second.txt')
    batch_publisher.side_effect = [RuntimeError('synthetic failure'), {'status': 'processed', 'code': 'published'}]
    assert run_async(publish_documents.run(tmp_path, batch_arguments([first, second]))) == 2
    assert [call.args[1] for call in batch_publisher.await_args_list] == [first, second]
    output = capsys.readouterr().out
    assert 'publication_failed' in output and 'published' in output
