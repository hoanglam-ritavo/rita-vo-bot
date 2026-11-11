import asyncio
import io
import runpy
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic import command
from alembic.config import Config

import main as entrypoint
from app.cli import check_db
from app.database import config, session
from app.database.migrations import include_object
from app.database.runtime import run_async


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    for key in ('DATABASE_URL', 'MIGRATION_DATABASE_URL'):
        monkeypatch.delenv(key, raising=False)


def test_environment_overrides_dotenv(monkeypatch, tmp_path):
    (tmp_path / '.env').write_text('DATABASE_URL=postgresql+psycopg://u:p@local/from_file')
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://u:p@remote/from_env')
    assert config.database_url().database == 'from_env'
    assert config.database_url(migration=True) == config.database_url()
    monkeypatch.setenv('MIGRATION_DATABASE_URL', 'postgresql+psycopg://u:p@remote/migration')
    assert config.database_url(migration=True).database == 'migration'


@pytest.mark.parametrize('value', [None, '', 'password-secret',
    'sqlite:///secret', 'postgresql+psycopg://u:secret@host:bad/db',
    'postgresql+psycopg://u:secret@host'])
def test_invalid_configuration_is_redacted(value):
    with pytest.raises(config.ConfigurationError) as error:
        config.validate_url(value)
    assert 'secret' not in str(error.value)


def test_lazy_engine_and_disposal(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://u:p@localhost/test')
    assert session._engine is None
    engine = session.get_engine()
    assert engine.echo is False
    assert session.get_engine() is engine
    run_async(session.close_database())
    assert session._engine is None


@pytest.mark.parametrize('error', [None, config.ConfigurationError('DATABASE_URL is required.'),
    session.DatabaseCheckError('pgvector is not enabled.'),
    RuntimeError('password-secret')])
def test_entrypoint_always_closes(monkeypatch, capsys, error):
    check = AsyncMock(return_value='test-version', side_effect=error)
    close = AsyncMock()
    monkeypatch.setattr(check_db, 'check_database', check)
    monkeypatch.setattr(check_db, 'close_database', close)
    assert run_async(check_db.main()) == (1 if error else 0)
    check.assert_awaited_once()
    close.assert_awaited_once()
    captured = capsys.readouterr()
    assert 'password-secret' not in captured.out + captured.err
    if error is None:
        assert captured.out == 'Database OK; pgvector test-version.\n'
        assert captured.err == ''
    else:
        assert captured.out == ''
        expected = (str(error) if isinstance(error, (config.ConfigurationError, session.DatabaseCheckError))
                    else 'Database check failed. Check connectivity, credentials and TLS settings.')
        assert captured.err == expected + '\n'


@pytest.mark.parametrize('exit_code', [0, 1])
def test_main_entrypoint_delegates(monkeypatch, exit_code):
    from app.cli.main import main as dispatch
    assert entrypoint.main is dispatch
    monkeypatch.setattr(sys, 'argv', ['main.py'])
    main = AsyncMock(return_value=exit_code)
    monkeypatch.setattr(check_db, 'main', main)
    with pytest.raises(SystemExit) as result:
        runpy.run_path('main.py', run_name='__main__')
    assert result.value.code == exit_code
    main.assert_awaited_once()


def test_entrypoint_imports_have_no_database_side_effects():
    script = '''
from unittest.mock import patch
with patch('sqlalchemy.ext.asyncio.create_async_engine', side_effect=AssertionError('Unexpected engine creation')), \\
     patch('dotenv.load_dotenv', side_effect=AssertionError('Unexpected configuration loading')):
    import app.cli.check_db
    import main as entrypoint
    import app.cli.ingest
    import app.cli.review
    import app.cli.import_catalog
    import app.database.models
    import app.database.migrations
    import app.ingestion.chunking
    import app.ingestion.embeddings
    import app.ingestion.artifacts
    assert callable(entrypoint.main)
    assert "pymupdf" not in __import__("sys").modules
    assert "sentence_transformers" not in __import__("sys").modules
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ''
    assert result.stderr == ''


def test_selector_loop_on_windows():
    async def check():
        import sys
        if sys.platform == 'win32':
            assert isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
    run_async(check())


def test_unmanaged_tables_are_excluded():
    assert not include_object(None, 'external', 'table', True, None)
    assert include_object(None, 'new', 'table', False, None)
    assert include_object(None, 'managed', 'table', True, object())


@pytest.mark.parametrize('version', [None, '0.8.0'])
def test_extension_healthcheck(monkeypatch, version):
    connection = AsyncMock()
    connection.scalar.return_value = version
    engine = MagicMock()
    engine.connect.return_value.__aenter__.return_value = connection
    monkeypatch.setattr(session, 'get_engine', lambda: engine)
    if version is None:
        with pytest.raises(session.DatabaseCheckError, match='pgvector is not enabled'):
            run_async(session.check_database())
    else:
        assert run_async(session.check_database()) == version
    engine.connect.return_value.__aexit__.assert_awaited_once()


def test_migration_offline_sql(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://u:secret@localhost/test')
    output = io.StringIO()
    cfg = Config('alembic.ini', output_buffer=output)
    command.upgrade(cfg, 'head', sql=True)
    assert 'CREATE EXTENSION IF NOT EXISTS vector' in output.getvalue()
    assert 'secret' not in output.getvalue()
    output.seek(0)
    output.truncate()
    command.downgrade(cfg, '0001_enable_vector:base', sql=True)
    assert 'DROP EXTENSION' not in output.getvalue()
