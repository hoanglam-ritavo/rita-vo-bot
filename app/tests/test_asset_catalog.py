"""Synthetic catalog fixtures; no private documents or database access."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.catalog import inventory, validation, storage, reports


@pytest.fixture
def workspace(tmp_path):
    assets = tmp_path / 'app/assets'
    assets.mkdir(parents=True)
    (assets / 'policy.pdf').write_bytes(b'synthetic policy')
    return tmp_path












@pytest.mark.parametrize('value', ['../outside.pdf', '/absolute.pdf', 'C:/a.pdf',
                                  'a\\b.pdf', 'a//b.pdf', 'a/./b.pdf', 'a:stream', 'a./b.pdf'])
def test_reject_unsafe_paths(workspace, value):
    data = validation.new_catalog(inventory.inventory(workspace / 'app/assets'))
    data['documents'][0]['source_path'] = value
    assert any('source_path' in error for error in validation.validate_schema(data))


def test_reject_symlink(workspace):
    link = workspace / 'app/assets/link.pdf'
    try:
        link.symlink_to(workspace / 'app/assets/policy.pdf')
    except OSError:
        pytest.skip('Creating symlinks requires OS permission; reparse-point rejection tested separately.')
    with pytest.raises(inventory.CatalogError, match='Link'):
        inventory.inventory(workspace / 'app/assets')


def test_reject_windows_reparse_point(workspace, monkeypatch):
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        if path.name == 'policy.pdf':
            return SimpleNamespace(st_mode=stat.S_IFREG,
                                   st_file_attributes=0x400)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(inventory.CatalogError, match='reparse'):
        inventory.inventory(workspace / 'app/assets')


@pytest.mark.parametrize('field,value', [
    ('size_bytes', True), ('sha256', 'invalid'), ('lifecycle_status', []),
    ('evidence_notes', 'not a list'), ('relations', [{'type': [], 'revision_id': 'bad'}]),
    ('effective_from', '2025-02-30'), ('access_groups', ['']), ('owner', 5),
])
def test_malformed_fields_are_validation_errors(workspace, field, value):
    data = validation.new_catalog(inventory.inventory(workspace / 'app/assets'))
    data['documents'][0][field] = value
    assert reports.build_report('validate', data, {})['exit_code'] == 1


def test_duplicate_ids_and_relations(workspace):
    data = validation.new_catalog(inventory.inventory(workspace / 'app/assets'))
    first = data['documents'][0]
    second = validation.new_record('second.pdf', {k: first[k] for k in ('sha256', 'size_bytes', 'extension')})
    data['documents'].append(second)
    second['document_id'] = first['document_id']
    assert any('duplicate document_id' in e for e in validation.validate_schema(data))
    second['document_id'] = str(uuid4())
    second['revision_id'] = first['revision_id']
    assert any('duplicate revision_id' in e for e in validation.validate_schema(data))
    second['revision_id'] = str(uuid4())
    first['relations'] = [{'type': 'duplicate_of', 'revision_id': second['revision_id']}]
    assert validation.validate_schema(data) == []
    second['sha256'] = '0' * 64
    assert any('hashes differ' in e for e in validation.validate_schema(data))
    first['relations'][0]['revision_id'] = str(uuid4())
    assert any('relation target' in e for e in validation.validate_schema(data))
    first['relations'][0]['revision_id'] = first['revision_id']
    assert any('self-referencing' in e for e in validation.validate_schema(data))
    first['relations'] = [{'type': 'supersedes', 'revision_id': second['revision_id']}]
    second['relations'] = [{'type': 'supersedes', 'revision_id': first['revision_id']}]
    errors = validation.validate_schema(data)
    assert any('Cycle' in e for e in errors)


def test_readiness_and_effective_dates(workspace):
    data = validation.new_catalog(inventory.inventory(workspace / 'app/assets'))
    record = data['documents'][0]
    assert validation.validate_schema(data) == []
    record['indexing_decision'] = 'include'
    assert validation.validate_schema(data)
    record.update(lifecycle_status='active', owner='Synthetic owner',
                  applicability='Synthetic team', access_groups=['synthetic-readers'],
                  evidence_notes=['Applicability confirmed by synthetic owner.'])
    assert validation.validate_schema(data) == []
    record.update(effective_from='2099-01-01', effective_to='2000-01-01')
    assert any('reversed' in e for e in validation.validate_schema(data))
    assert any('outside' in e for e in validation.validate_schema(data))


def test_duplicate_review_does_not_merge_rights(workspace):
    (workspace / 'app/assets/copy.pdf').write_bytes(b'synthetic policy')
    files = inventory.inventory(workspace / 'app/assets')
    data = validation.new_catalog(files)
    for record in data['documents']:
        record.update(indexing_decision='exclude')
    data['documents'][0]['access_groups'] = ['synthetic-a']
    data['documents'][1]['access_groups'] = ['synthetic-b']
    report = reports.build_report('scan', data, files)
    assert report['exit_code'] == 0
    assert len(report['duplicates']) == 1
    assert data['documents'][0]['document_id'] != data['documents'][1]['document_id']


def test_unknown_format_and_thumbnail(workspace):
    (workspace / 'app/assets/Thumbs.db').write_bytes(b'ignored')
    (workspace / 'app/assets/input.unknown').write_bytes(b'a,b')
    files = inventory.inventory(workspace / 'app/assets')
    assert 'Thumbs.db' not in files
    report = reports.build_report('scan', validation.new_catalog(files), files)
    assert report['unrecognized_formats'] == ['input.unknown']
    assert report['exit_code'] == 1


def test_atomic_failures_and_no_clobber(tmp_path, monkeypatch):
    path = tmp_path / 'catalog.json'
    path.write_text('old', encoding='utf-8')
    with pytest.raises(FileExistsError):
        storage.atomic_write(path, 'new', create_only=True)
    assert path.read_text() == 'old'
    def fail(*args):
        raise OSError('synthetic failure')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError):
        storage.atomic_write(path, 'new')
    assert path.read_text() == 'old'
    assert list(tmp_path.iterdir()) == [path]










def test_cli_import_does_not_import_database():
    result = subprocess.run([sys.executable, '-c',
                             'import sys; import app.cli.asset_catalog; '
                             'assert "app.database" not in sys.modules; '
                             'assert "sqlalchemy" not in sys.modules'],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
