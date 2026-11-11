import io
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config

from app.asset_catalog import inventory, new_catalog
from app import catalog_import as importer
from app.cli import import_catalog as cli
from app.db.models import DocumentRevision
from app.db.runtime import run_async


@pytest.fixture
def catalog_root(tmp_path):
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets/a.pdf').write_bytes(b'synthetic')
    catalog = new_catalog(inventory(tmp_path / 'assets'))
    (tmp_path / 'data/catalog').mkdir(parents=True)
    (tmp_path / 'data/catalog/documents.json').write_text(json.dumps(catalog), encoding='utf-8')
    return tmp_path, catalog


def test_preflight_and_mapping(catalog_root):
    root, catalog = catalog_root
    assert importer.prepare_catalog(root) == (catalog, 0)
    record = catalog['documents'][0]
    record.update(effective_from='2025-01-01', access_groups=['staff'])
    values = importer.revision_values(record)
    assert values['document_id'] == UUID(record['document_id'])
    assert values['effective_from'] == date(2025, 1, 1)
    assert values['indexing_decision'] == 'hold'
    assert 'access_groups' not in values
    assert 'relations' not in values
    assert values['source_path_key'] == record['source_path'].casefold()


@pytest.mark.parametrize('change', ['added', 'missing', 'changed', 'schema'])
def test_preflight_blocks(catalog_root, change):
    root, catalog = catalog_root
    if change == 'added':
        (root / 'assets/b.pdf').write_bytes(b'new')
    elif change == 'missing':
        (root / 'assets/a.pdf').unlink()
    elif change == 'changed':
        (root / 'assets/a.pdf').write_bytes(b'different')
    else:
        catalog['documents'][0]['review_status'] = 'invalid'
        (root / 'data/catalog/documents.json').write_text(json.dumps(catalog))
    with pytest.raises(importer.ImportRejected):
        importer.prepare_catalog(root)


def test_duplicates_and_unknown_formats_are_warnings(catalog_root):
    root, _ = catalog_root
    (root / 'assets/c.unknown').write_bytes(b'synthetic')
    catalog = new_catalog(inventory(root / 'assets'))
    (root / 'data/catalog/documents.json').write_text(json.dumps(catalog))
    assert importer.prepare_catalog(root)[1] == 2


def mock_session(monkeypatch, revisions=()):
    db = MagicMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    results = []
    for items in ([], revisions, [], []):
        result = MagicMock()
        result.all.return_value = list(items)
        results.append(result)
    db.scalars = AsyncMock(side_effect=results)
    @asynccontextmanager
    async def scope():
        yield db
    monkeypatch.setattr(importer, 'session_scope', scope)
    return db


@pytest.mark.parametrize('decision', ['hold', 'exclude'])
def test_dry_run_is_read_only(monkeypatch, catalog_root, decision):
    _, catalog = catalog_root
    catalog['documents'][0]['indexing_decision'] = decision
    db = mock_session(monkeypatch)
    result = run_async(importer.synchronize(catalog, dry_run=True))
    assert (result.documents_added, result.revisions_added) == (1, 1)
    db.flush.assert_not_awaited()
    db.add.assert_not_called()
    db.add_all.assert_not_called()
    assert db.execute.await_count == 1
    assert 'READ ONLY' in str(db.execute.call_args.args[0])


@pytest.mark.parametrize('field,value', [('sha256', 'f' * 64), ('size_bytes', 999),
                                       ('document_id', UUID(int=1))])
def test_identity_conflict_before_writes(monkeypatch, catalog_root, field, value):
    _, catalog = catalog_root
    record = catalog['documents'][0]
    revision = DocumentRevision(id=UUID(record['revision_id']), **importer.revision_values(record))
    setattr(revision, field, value)
    db = mock_session(monkeypatch, [revision])
    with pytest.raises(importer.ImportRejected):
        run_async(importer.synchronize(catalog))
    db.flush.assert_not_awaited()
    db.add.assert_not_called()


@pytest.mark.parametrize('error,code', [(None, 0), (importer.ImportRejected('Rejected.'), 1),
                                     (RuntimeError('private-secret'), 2)])
def test_cli_exit_and_cleanup(monkeypatch, capsys, catalog_root, error, code):
    root, _ = catalog_root
    monkeypatch.setattr(cli, 'synchronize', AsyncMock(return_value=importer.ImportResult(), side_effect=error))
    close = AsyncMock()
    monkeypatch.setattr(cli, 'close_database', close)
    assert run_async(cli.run(root)) == code
    close.assert_awaited_once()
    output = capsys.readouterr()
    assert 'private-secret' not in output.out + output.err


def test_entrypoint_import_is_lazy():
    script = """
from unittest.mock import patch
with patch('sqlalchemy.ext.asyncio.create_async_engine', side_effect=AssertionError), patch('dotenv.load_dotenv', side_effect=AssertionError):
    import app.cli.import_catalog
"""
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_reviewed_migration_sql(monkeypatch):
    monkeypatch.setenv('MIGRATION_DATABASE_URL', 'postgresql+psycopg://u:p@localhost/synthetic')
    output = io.StringIO()
    config = Config('alembic.ini', output_buffer=output)
    command.upgrade(config, 'head', sql=True)
    sql = output.getvalue()
    assert sql.count('CREATE TABLE ') == 7  # Six application tables plus Alembic.
    assert 'VECTOR(384)' in sql
    assert 'WHERE is_catalog_current' in sql
    assert 'WHERE is_active' in sql
    output.seek(0)
    output.truncate()
    command.downgrade(config, '0002_type_a_catalog:0001_enable_vector', sql=True)
    assert output.getvalue().count('DROP TABLE ') == 6
    assert 'DROP EXTENSION' not in output.getvalue()
