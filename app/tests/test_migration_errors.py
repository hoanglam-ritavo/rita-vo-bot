"""Migration diagnostics using synthetic failures, without database connections."""
import runpy
from unittest.mock import AsyncMock, MagicMock

import psycopg
import pytest
from alembic import context
from alembic.script.revision import MultipleHeads, ResolutionError
from alembic.util.exc import CommandError, DatabaseNotAtHead
from sqlalchemy import exc as sa_exc

from app.database import config
from app.database.migrations.errors import Stage, format_error


SECRET = 'postgresql://private-user:private-password@private-host/private-db'


@pytest.mark.parametrize(('state', 'code'), [
    ('28P01', 'DB_AUTH'), ('28000', 'DB_AUTH'), ('08006', 'DB_CONNECT'),
    ('42501', 'DB_PERMISSION'), ('3D000', 'DB_UNAVAILABLE'),
    ('42P07', 'DB_SCHEMA_CONFLICT'), ('42701', 'DB_SCHEMA_CONFLICT'),
    ('42710', 'DB_SCHEMA_CONFLICT'), ('42P01', 'DB_SCHEMA_CONFLICT'),
    ('42703', 'DB_SCHEMA_CONFLICT'), ('42704', 'DB_SCHEMA_CONFLICT'),
    ('23505', 'DB_EXECUTION'), ('0A000', 'DB_EXECUTION'),
])
@pytest.mark.parametrize('wrapped', [False, True])
def test_database_classification_and_redaction(state, code, wrapped):
    error = psycopg.errors.lookup(state)(SECRET)
    if wrapped:
        error = sa_exc.OperationalError('SELECT private-content', {'password': SECRET}, error)
    message = format_error(error, Stage.EXECUTION)
    assert message.startswith(f'[{code}] Alembic execution failed.')
    assert f'SQLSTATE={state}.' in message
    assert SECRET not in message
    assert 'private-' not in message
    assert 'SELECT' not in message
    assert 'pgvector' not in message


@pytest.mark.parametrize(('error', 'code'), [
    (CommandError('Target database is not up to date.'), 'ALEMBIC_NOT_AT_HEAD'),
    (DatabaseNotAtHead(SECRET), 'ALEMBIC_NOT_AT_HEAD'),
    (ResolutionError(SECRET, SECRET), 'ALEMBIC_REVISION'),
    (MultipleHeads([SECRET], SECRET), 'ALEMBIC_REVISION'),
    (CommandError(SECRET), 'ALEMBIC_COMMAND'),
    (RuntimeError(SECRET), 'MIGRATION_UNKNOWN'),
])
def test_alembic_and_unknown_errors(error, code):
    message = format_error(error, Stage.EXECUTION)
    assert message.startswith(f'[{code}]')
    assert SECRET not in message
    assert 'SQLSTATE' not in message


def test_wrapped_revision_and_bounded_cycles():
    error = CommandError(SECRET)
    error.__cause__ = ResolutionError(SECRET, SECRET)
    error.__cause__.__cause__ = error
    assert '[ALEMBIC_REVISION]' in format_error(error, Stage.EXECUTION)
    chain = RuntimeError(SECRET)
    for _ in range(50):
        outer = RuntimeError(SECRET)
        outer.__cause__ = chain
        chain = outer
    assert '[MIGRATION_UNKNOWN]' in format_error(chain, Stage.EXECUTION)


def test_invalid_sqlstate_and_suppressed_context_are_not_exposed():
    error = psycopg.OperationalError(SECRET)
    error.sqlstate = SECRET
    assert 'SQLSTATE' not in format_error(error, Stage.CONNECTION)
    outer = RuntimeError(SECRET)
    outer.__context__ = psycopg.errors.InsufficientPrivilege(SECRET)
    outer.__suppress_context__ = True
    assert '[MIGRATION_UNKNOWN]' in format_error(outer, Stage.EXECUTION)


@pytest.mark.parametrize('error', [psycopg.OperationalError(SECRET), TimeoutError(SECRET)])
def test_connection_without_state_does_not_guess(error):
    message = format_error(error, Stage.CONNECTION)
    assert '[DB_CONNECT]' in message
    assert 'authentication failed' not in message
    assert 'pgvector' not in message
    assert SECRET not in message


@pytest.fixture
def migration_environment(monkeypatch):
    import sqlalchemy.ext.asyncio

    engine = MagicMock()
    connection = MagicMock()
    connection.run_sync = AsyncMock(side_effect=lambda callback: callback(connection))
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
    engine.dispose = AsyncMock()
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr(sqlalchemy.ext.asyncio, 'create_async_engine', factory)
    monkeypatch.setattr(config, 'database_url', lambda **kwargs: 'synthetic-url')
    monkeypatch.setattr(context, 'is_offline_mode', lambda: False)
    monkeypatch.setattr(context, 'configure', MagicMock())
    monkeypatch.setattr(context, 'begin_transaction', MagicMock())
    monkeypatch.setattr(context, 'run_migrations', MagicMock())
    return engine, connection, factory


def execute_environment():
    return runpy.run_module('app.database.migrations.env')


@pytest.mark.parametrize('failure', ['none', 'connect', 'execute', 'cleanup', 'both', 'engine', 'config'])
def test_environment_stages_and_cleanup(migration_environment, monkeypatch, failure):
    engine, connection, factory = migration_environment
    error = psycopg.errors.InsufficientPrivilege(SECRET)
    expected_stage = None
    if failure == 'connect':
        engine.connect.return_value.__aenter__.side_effect = error
        expected_stage = 'Database connection'
    elif failure in ('execute', 'both'):
        context.run_migrations.side_effect = error
        expected_stage = 'Alembic execution'
    elif failure == 'engine':
        factory.side_effect = RuntimeError(SECRET)
        expected_stage = 'Engine creation'
    elif failure == 'config':
        monkeypatch.setattr(config, 'database_url', MagicMock(side_effect=RuntimeError(SECRET)))
        expected_stage = 'Configuration'
    if failure in ('cleanup', 'both'):
        engine.dispose.side_effect = RuntimeError(SECRET)
        expected_stage = expected_stage or 'Engine cleanup'
    if failure == 'none':
        execute_environment()
        context.configure.assert_called_once()
        context.begin_transaction.assert_called_once()
        context.run_migrations.assert_called_once()
    else:
        with pytest.raises(SystemExit) as result:
            execute_environment()
        message = str(result.value)
        assert f'{expected_stage} failed.' in message
        assert SECRET not in message
        assert result.value.__suppress_context__
        if failure == 'both':
            assert '[DB_PERMISSION]' in message
    if failure in ('engine', 'config'):
        engine.dispose.assert_not_awaited()
    else:
        engine.dispose.assert_awaited_once()


def test_configuration_message_preserved(migration_environment, monkeypatch):
    monkeypatch.setattr(config, 'database_url', MagicMock(
        side_effect=config.ConfigurationError('DATABASE_URL is required.')))
    with pytest.raises(SystemExit, match='DATABASE_URL is required.'):
        execute_environment()


def test_autogenerate_failure_uses_safe_message(migration_environment):
    context.run_migrations.side_effect = CommandError('Target database is not up to date.')
    with pytest.raises(SystemExit, match='ALEMBIC_NOT_AT_HEAD'):
        execute_environment()


def test_upstream_autogenerate_not_at_head_contract():
    from alembic.autogenerate.api import RevisionContext
    from alembic.config import Config

    script = MagicMock()
    script.get_revisions.side_effect = [('old',), ('new',)]
    revision = RevisionContext(Config(), script, {
        'sql': False, 'rev_id': None, 'message': None, 'head': 'head',
        'splice': False, 'branch_label': None, 'version_path': None, 'depends_on': None,
    })
    with pytest.raises(CommandError) as result:
        revision.run_autogenerate(('old',), MagicMock())
    assert '[ALEMBIC_NOT_AT_HEAD]' in format_error(result.value, Stage.EXECUTION)


def test_check_differences_keep_alembic_behavior(monkeypatch):
    from alembic import command
    from alembic.config import Config
    from alembic.operations.ops import CreateTableOp
    from alembic.script import ScriptDirectory
    from alembic.util.exc import AutogenerateDiffsDetected

    script = MagicMock()
    monkeypatch.setattr(ScriptDirectory, 'from_config', lambda config: script)

    def simulate_environment():
        # check() raises its difference result after env.py returns successfully.
        revision = context.get_context().opts['revision_context']
        revision.generated_revisions[0].upgrade_ops.ops.append(CreateTableOp('synthetic', []))

    # The real EnvironmentContext has no MigrationContext until configure() is called.
    def run_environment():
        context.configure(dialect_name='postgresql')
        simulate_environment()

    script.run_env.side_effect = run_environment
    with pytest.raises(AutogenerateDiffsDetected):
        command.check(Config())


def test_cli_exit_and_stderr_without_traceback():
    import subprocess
    import sys

    script = '''
import runpy
from unittest.mock import patch
with patch('app.database.config.database_url', return_value='synthetic-url'), \\
     patch('alembic.context.is_offline_mode', return_value=False), \\
     patch('sqlalchemy.ext.asyncio.create_async_engine', side_effect=RuntimeError('private-password')):
    runpy.run_module('app.database.migrations.env')
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == ''
    assert '[MIGRATION_UNKNOWN] Engine creation failed.' in result.stderr
    assert 'private-password' not in result.stderr
    assert 'Traceback' not in result.stderr


def test_diagnostics_import_has_no_side_effects():
    import subprocess
    import sys

    script = '''
from unittest.mock import patch
with patch('sqlalchemy.ext.asyncio.create_async_engine', side_effect=AssertionError('engine')), \\
     patch('dotenv.load_dotenv', side_effect=AssertionError('configuration')):
    import app.database.migrations.errors
'''
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ''
