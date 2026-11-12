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
from test_document_packages import approved


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


def test_review_validation_and_approval_use_new_root(tmp_path, monkeypatch, capsys):
    _, _, review, approval = approved(tmp_path)
    assert review.is_relative_to(tmp_path / 'app' / 'chunks' / 'review')
    assert approval.is_relative_to(tmp_path / 'app' / 'chunks' / 'approved')
    monkeypatch.setattr(prepare_documents, 'REPOSITORY_ROOT', tmp_path)
    assert prepare_documents.main(['validate', '--review', str(review), '--json']) == 0
    assert 'valid_review' in capsys.readouterr().out
    _, payload, _, _ = p.load_approval(tmp_path, approval)
    assert payload['chunks'][0]['content'] == 'Human corrected policy.'
    assert not (tmp_path / 'indexing_chunks').exists()


def test_batch_uses_new_approvals_and_preserves_historical_packages(tmp_path, monkeypatch, capsys):
    import app.database

    _, _, review, approval = approved(tmp_path)
    old = tmp_path / 'indexing_chunks'
    shutil.copytree(paths.workspace(tmp_path), old)
    before = {f.relative_to(old): f.read_bytes() for f in old.rglob('*') if f.is_file()}
    # A copied approval with the same identity must not be considered a second candidate.
    assert p.review_paths(tmp_path) == sorted([review.parent / entry.name for entry in review.parent.iterdir()])
    assert all(path.is_relative_to(paths.workspace(tmp_path)) for path in p.review_paths(tmp_path))
    publish = AsyncMock(return_value={'status': 'processed', 'code': 'dry_run'})
    monkeypatch.setattr(approved_publication, 'publish', publish)
    monkeypatch.setattr(app.database, 'close_database', AsyncMock())
    args = SimpleNamespace(all=True, approved=None, model_revision='a' * 40,
                           model_path=tmp_path, dry_run=True, force=False,
                           update_metadata=False, json=True)
    assert run_async(publish_documents.run(tmp_path, args)) == 0
    assert publish.await_count == 1
    assert publish.await_args.args[1] == approval
    capsys.readouterr()
    with pytest.raises(p.Rejected, match='approval_outside_workspace'):
        p.load_approval(tmp_path, old / approval.relative_to(paths.workspace(tmp_path)))
    # With only old approvals left, there is no fallback to the historical root.
    shutil.rmtree(paths.workspace(tmp_path))
    publish.reset_mock()
    assert p.review_paths(tmp_path) == []
    assert run_async(publish_documents.run(tmp_path, args)) == 1
    assert 'no_approvals' in capsys.readouterr().out
    publish.assert_not_called()
    assert before == {f.relative_to(old): f.read_bytes() for f in old.rglob('*') if f.is_file()}
