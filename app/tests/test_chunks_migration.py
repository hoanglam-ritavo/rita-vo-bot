"""Synthetic migration and retired-command checks."""
import json
from pathlib import Path
import pytest
from app.ingestion import document_packages as p
from app.catalog.inventory import inventory
from app.catalog.validation import new_catalog
from app.paths import assets, workspace
from scripts.migrate_indexing_chunks import migrate


def legacy(root):
    assets(root).mkdir(parents=True)
    (assets(root) / 'sample.doc').write_bytes(b'synthetic')
    catalog = new_catalog(inventory(assets(root)))
    old = root / 'indexing_workspace'
    (old / 'catalog').mkdir(parents=True)
    (old / 'catalog/documents.json').write_bytes(p.encoded(catalog))
    (old / 'archive').mkdir()
    (old / 'archive/old.txt').write_bytes(b'discarded history')
    return catalog


def test_migration_preserves_metadata_and_removes_old(tmp_path):
    catalog = legacy(tmp_path)
    assert migrate(tmp_path)['code'] == 'migration_preview'
    assert not workspace(tmp_path).exists()
    assert migrate(tmp_path, True)['records'] == 1
    assert not (tmp_path / 'indexing_workspace').exists()
    assert set(x.name for x in workspace(tmp_path).iterdir()) == {'review', 'approved'}
    path, = p.review_paths(tmp_path)
    assert p.metadata(path) == catalog['documents'][0]
    with pytest.raises(p.Rejected):
        p.approve(tmp_path, path, 'Reviewer')
    assert migrate(tmp_path, True)['code'] == 'already_migrated'


def test_migration_unknown_entry_blocks_delete(tmp_path):
    legacy(tmp_path)
    (tmp_path / 'indexing_workspace/unexpected').write_bytes(b'unknown')
    with pytest.raises(p.Rejected, match='unknown_workspace'):
        migrate(tmp_path, True)
    assert (tmp_path / 'indexing_workspace/catalog/documents.json').exists()


def test_resume_after_partial_delete(tmp_path, monkeypatch):
    legacy(tmp_path)
    original = Path.unlink
    calls = []
    def interrupted(path, *args, **kwargs):
        if 'indexing_workspace' in path.parts:
            calls.append(path)
            if len(calls) == 2:
                raise OSError('synthetic interruption')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', interrupted)
    with pytest.raises(OSError):
        migrate(tmp_path, True)
    monkeypatch.setattr(Path, 'unlink', original)
    assert migrate(tmp_path, True)['code'] == 'migration_resumed'
    assert not (tmp_path / 'indexing_workspace').exists()


def test_init_idempotence_and_drift(tmp_path):
    assets(tmp_path).mkdir(parents=True)
    source = assets(tmp_path) / 'sample.txt'
    source.write_text('synthetic')
    assert len(p.initialize(tmp_path)) == 1
    assert p.initialize(tmp_path) == []
    source.write_text('changed')
    with pytest.raises(p.Rejected, match='source_changed'):
        p.initialize(tmp_path)


def test_retired_commands_do_not_write(tmp_path, monkeypatch):
    from app.cli import asset_catalog, import_catalog, ingest, review
    monkeypatch.chdir(tmp_path)
    for cli in (asset_catalog, import_catalog, ingest, review):
        assert cli.main(['anything']) == 2
    assert list(tmp_path.iterdir()) == []


def test_migration_conflict_and_source_drift(tmp_path):
    from uuid import uuid5
    from scripts.migrate_indexing_chunks import NAMESPACE
    record = legacy(tmp_path)['documents'][0]
    (assets(tmp_path) / record['source_path']).unlink()
    # Missing source metadata is retained, but cannot be extracted/approved.
    migrate(tmp_path, True)
    path, = p.review_paths(tmp_path)
    assert p.metadata(path) == record
    with pytest.raises(p.Rejected):
        p.source_for(tmp_path, record)


def test_metadata_identity_and_approval_tampering(tmp_path):
    from test_document_packages import approved
    _, _, review, approval = approved(tmp_path)
    data = p.metadata(review)
    data['sha256'] = '0'*64
    (review / 'metadata.json').write_bytes(p.encoded(data))
    with pytest.raises(p.Rejected, match='identity_changed'):
        p.metadata(review)
    with (approval / 'metadata.json').open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(p.Rejected, match='hash_mismatch'):
        p.load_approval(tmp_path, approval)


def test_unreadable_directory_is_not_silently_skipped(tmp_path, monkeypatch):
    from scripts.migrate_indexing_chunks import tree
    blocked = tmp_path / 'blocked'
    blocked.mkdir()
    original = Path.iterdir
    def denied(path):
        if path.name == 'blocked':
            raise PermissionError('synthetic')
        return original(path)
    monkeypatch.setattr(Path, 'iterdir', denied)
    with pytest.raises(PermissionError):
        tree(tmp_path)
