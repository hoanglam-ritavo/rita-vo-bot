"""Migration tests use synthetic bytes, including failure and crash recovery."""

import copy
import ctypes
import os
from pathlib import Path

import pytest

from app.catalog.storage import atomic_write, json_text, load_catalog
from app.catalog.inventory import inventory
from app.catalog.validation import new_catalog
from scripts import restructure_assets as migration


def test_cli_preserves_legacy_data_paths(monkeypatch, capsys):
    roots = []

    def preview(root):
        roots.append(root)
        return {'operations': [], 'create_dirs': [], 'remove_dirs': []}

    monkeypatch.setattr(migration, 'preview', preview)
    assert migration.main([]) == 0
    repository = Path(__file__).resolve().parents[2]
    assert roots == [repository / 'app']
    assert migration.paths(roots[0]) == (
        repository / 'app/assets',
        repository / 'app/data/catalog/documents.json',
        repository / 'app/data/reports/asset-restructure',
    )
    assert 'Files=0' in capsys.readouterr().out


@pytest.fixture
def workspace(tmp_path):
    assets, catalog, _ = migration.paths(tmp_path)
    assets.mkdir()
    for index, relative in enumerate(migration.MAPPING):
        path = assets / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f'synthetic-{index}'.encode())
    for relative in migration.EMPTY_DIRS:
        (assets / relative).mkdir(parents=True, exist_ok=True)
    (assets / 'unchanged.docx').write_bytes(b'unchanged')
    value = new_catalog(inventory(assets))
    value['documents'][0]['review_notes'] = ['Preserve manual metadata.']
    atomic_write(catalog, json_text(value))
    return tmp_path


def test_preview_apply_repeat_rollback(workspace):
    assets, catalog, runs = migration.paths(workspace)
    before_tree = migration.tree(assets)
    before_bytes = catalog.read_bytes()
    before = load_catalog(catalog)
    assert len(migration.preview(workspace)['operations']) == 15
    assert not runs.exists()
    assert migration.tree(assets) == before_tree
    run_id = migration.apply(workspace)
    expected = copy.deepcopy(before)
    for record in expected['documents']:
        record['source_path'] = migration.MAPPING.get(record['source_path'], record['source_path'])
    assert load_catalog(catalog) == expected
    assert not (set(migration.tree(assets)[1]) & migration.SOURCE_DIRS)
    assert migration.apply(workspace) is None
    assert len(list(runs.iterdir())) == 1
    migration.rollback(workspace, run_id)
    assert catalog.read_bytes() == before_bytes
    assert migration.tree(assets) == before_tree
    migration.rollback(workspace, run_id)


@pytest.mark.parametrize('problem', ['collision', 'unexpected-file', 'unexpected-directory', 'drift'])
def test_preflight_rejects(workspace, problem):
    assets, catalog, runs = migration.paths(workspace)
    if problem == 'collision':
        path = assets / next(iter(migration.MAPPING.values()))
    elif problem == 'unexpected-file':
        path = assets / next(iter(migration.MAPPING)).rsplit('/', 1)[0] / 'extra.pdf'
    elif problem == 'unexpected-directory':
        path = assets / '05-huong-dan-su-dung/device/new'
        path.mkdir()
        with pytest.raises(migration.CatalogError):
            migration.apply(workspace)
        assert not runs.exists()
        return
    else:
        path = assets / 'unchanged.docx'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'new content')
    if problem != 'drift':
        atomic_write(catalog, json_text(new_catalog(inventory(assets))))
    with pytest.raises(migration.CatalogError):
        migration.apply(workspace)
    assert not runs.exists()


def test_case_collision(workspace):
    assets, catalog, _ = migration.paths(workspace)
    target = assets / next(iter(migration.MAPPING.values())).upper()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'collision')
    atomic_write(catalog, json_text(new_catalog(inventory(assets))))
    with pytest.raises(migration.CatalogError):
        migration.apply(workspace)


@pytest.mark.parametrize('failure', ['move', 'catalog'])
def test_failure_recovers(workspace, monkeypatch, failure):
    assets, catalog, runs = migration.paths(workspace)
    before = migration.tree(assets)
    catalog_before = catalog.read_bytes()
    original_move = migration.move
    original_write = migration.atomic_write
    calls = 0
    def failing_move(source, destination):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError('synthetic move failure')
        original_move(source, destination)
    def failing_write(path, text, **kwargs):
        nonlocal calls
        if path == catalog:
            calls += 1
            if calls == 1:
                raise OSError('synthetic catalog failure')
        original_write(path, text, **kwargs)
    monkeypatch.setattr(migration, 'move' if failure == 'move' else 'atomic_write',
                        failing_move if failure == 'move' else failing_write)
    with pytest.raises(OSError):
        migration.apply(workspace)
    assert migration.tree(assets) == before
    assert catalog.read_bytes() == catalog_before
    assert load_catalog(next(runs.glob('*/journal.json')))['status'] == 'rolled-back'


def test_crash_between_move_and_journal_is_recoverable(workspace, monkeypatch):
    assets, catalog, runs = migration.paths(workspace)
    before = migration.tree(assets)
    original = migration.move
    def crash(source, destination):
        original(source, destination)
        raise KeyboardInterrupt
    monkeypatch.setattr(migration, 'move', crash)
    with pytest.raises(KeyboardInterrupt):
        migration.apply(workspace)
    monkeypatch.setattr(migration, 'move', original)
    with pytest.raises(migration.CatalogError, match='unfinished'):
        migration.apply(workspace)
    migration.rollback(workspace, next(runs.iterdir()).name)
    assert migration.tree(assets) == before


@pytest.mark.parametrize('change', ['file', 'catalog', 'occupied-old', 'new-directory'])
def test_rollback_refuses_subsequent_changes(workspace, change):
    assets, catalog, _ = migration.paths(workspace)
    run_id = migration.apply(workspace)
    if change == 'file':
        (assets / next(iter(migration.MAPPING.values()))).write_bytes(b'edited')
    elif change == 'catalog':
        value = load_catalog(catalog)
        value['documents'][0]['review_notes'].append('New review')
        atomic_write(catalog, json_text(value))
    elif change == 'occupied-old':
        path = assets / next(iter(migration.MAPPING))
        path.parent.mkdir(parents=True)
        path.write_bytes(b'other')
    else:
        (assets / 'new-folder').mkdir()
    before = migration.tree(assets)
    with pytest.raises(migration.CatalogError):
        migration.rollback(workspace, run_id)
    assert migration.tree(assets) == before


def test_new_content_in_source_directory_is_not_deleted(workspace, monkeypatch):
    assets, _, _ = migration.paths(workspace)
    original = migration.move
    unexpected = assets / '05-huong-dan-su-dung/device/new.txt'
    def concurrent_write(source, destination):
        original(source, destination)
        unexpected.write_bytes(b'concurrent content')
    monkeypatch.setattr(migration, 'move', concurrent_write)
    with pytest.raises(migration.CatalogError, match='Recovery incomplete'):
        migration.apply(workspace)
    assert unexpected.read_bytes() == b'concurrent content'


def test_paths_outside_assets_rejected(workspace):
    with pytest.raises(migration.CatalogError):
        migration.asset_path(workspace / 'assets', '../outside')


@pytest.mark.skipif(os.name != 'nt', reason='Windows file attributes')
def test_hidden_attribute_preserved(workspace):
    assets, _, _ = migration.paths(workspace)
    old = next(iter(migration.MAPPING))
    source = assets / old
    original = source.stat().st_file_attributes
    assert ctypes.windll.kernel32.SetFileAttributesW(str(source), original | 2)
    run_id = migration.apply(workspace)
    assert (assets / migration.MAPPING[old]).stat().st_file_attributes & 2
    migration.rollback(workspace, run_id)
    assert source.stat().st_file_attributes & 2


def test_link_rejected(workspace, monkeypatch):
    original = migration.reject_links
    def reject(path):
        if path.name == 'unchanged.docx':
            raise migration.CatalogError('Link or reparse point rejected.')
        original(path)
    monkeypatch.setattr(migration, 'reject_links', reject)
    with pytest.raises(migration.CatalogError, match='reparse'):
        migration.apply(workspace)
