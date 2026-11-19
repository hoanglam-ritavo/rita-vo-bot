"""Exercise pytest's database configuration without connecting to PostgreSQL."""
import os
from unittest.mock import MagicMock

import pytest

from src.database import config as database_config
from src.tests import conftest, database_targets, test_employee_source_integration


CORPUS_URL = 'postgresql+psycopg://corpus_tester:fictional@localhost/corpus_test'
HRM_URL = 'postgresql+psycopg://hrm_tester:fictional@localhost/hrm_test'
RUNTIME_URL = 'postgresql+psycopg://runtime_user:fictional@localhost/app'


@pytest.fixture
def isolated_environment(monkeypatch, tmp_path):
    for name in (*database_targets.RUNTIME_URLS, *database_targets.TEST_URLS,
                 'TEST_HRM_DISPOSABLE_CONFIRMED', 'TEST_HRM_DATABASE_SCHEMA',
                 'PYTHON_DOTENV_DISABLED', 'TEST_PRIVATE_MARKER', 'FIXTURE_DB_NAME'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(database_targets, 'ROOT', tmp_path)
    monkeypatch.setattr(database_config, 'ROOT', tmp_path)
    return tmp_path / '.env'


@pytest.mark.parametrize('name', database_targets.TEST_URLS)
def test_dotenv_cannot_activate_an_integration_group(isolated_environment, name):
    isolated_environment.write_text(f'{name}={CORPUS_URL}\n', encoding='utf-8')
    with pytest.raises(pytest.skip.Exception, match=name):
        database_targets.require_disposable_database(name)
    assert name not in os.environ


def test_file_and_process_targets_checked_independently(monkeypatch, isolated_environment):
    isolated_environment.write_text(f'HRM_DATABASE_URL={HRM_URL}\n', encoding='utf-8')
    monkeypatch.setenv('HRM_DATABASE_URL', RUNTIME_URL)
    monkeypatch.setenv('TEST_HRM_DATABASE_URL',
                       'postgresql+psycopg://different_user:fictional@LOCALHOST:5432/hrm_test')
    with pytest.raises(pytest.fail.Exception, match='separate database from HRM_DATABASE_URL'):
        database_targets.require_disposable_database('TEST_HRM_DATABASE_URL')
    assert os.environ['HRM_DATABASE_URL'] == RUNTIME_URL


@pytest.mark.parametrize('target', ['file_database', 'process_database'])
def test_dotenv_interpolation_uses_both_loading_policies(monkeypatch, isolated_environment, target):
    isolated_environment.write_text(
        'FIXTURE_DB_NAME=file_database\n'
        'DATABASE_URL=postgresql+psycopg://runtime_user:fictional@localhost/${FIXTURE_DB_NAME}\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('FIXTURE_DB_NAME', 'process_database')
    monkeypatch.setenv('TEST_DATABASE_URL', f'postgresql+psycopg://tester:fictional@localhost/{target}')
    with pytest.raises(pytest.fail.Exception, match='separate database from DATABASE_URL'):
        database_targets.require_disposable_database('TEST_DATABASE_URL')


@pytest.mark.parametrize('other_name', ['DATABASE_URL', 'MIGRATION_DATABASE_URL', 'TEST_DATABASE_URL'])
def test_conversation_cannot_use_a_configured_database(monkeypatch, isolated_environment, other_name):
    monkeypatch.setenv(other_name, CORPUS_URL)
    monkeypatch.setenv('TEST_CONVERSATION_DATABASE_URL', CORPUS_URL)
    with pytest.raises(pytest.fail.Exception, match=f'separate database from {other_name}'):
        database_targets.require_disposable_database('TEST_CONVERSATION_DATABASE_URL')


def test_corpus_preflight_rejects_url_routing_overrides_before_connecting(monkeypatch, isolated_environment):
    engine_factory = MagicMock()
    monkeypatch.setattr(conftest, 'create_engine', engine_factory)
    monkeypatch.setenv('TEST_DATABASE_URL', CORPUS_URL + '?host=other-host')
    with pytest.raises(pytest.fail.Exception, match='TEST_DATABASE_URL must specify'):
        conftest.test_database.__wrapped__(monkeypatch)
    engine_factory.assert_not_called()


def test_corpus_migration_environment_is_scoped(monkeypatch, isolated_environment):
    isolated_environment.write_text(
        f'DATABASE_URL={RUNTIME_URL}\nTEST_PRIVATE_MARKER=file-only\n', encoding='utf-8',
    )
    monkeypatch.setenv('DATABASE_URL', RUNTIME_URL)
    monkeypatch.setenv('TEST_DATABASE_URL', CORPUS_URL)
    engine_factory = MagicMock()
    monkeypatch.setattr(conftest, 'create_engine', engine_factory)

    def upgrade(_config, revision):
        assert revision == 'head'
        assert database_config.database_url(migration=True).database == 'corpus_test'
        assert 'TEST_PRIVATE_MARKER' not in os.environ

    migration = MagicMock(side_effect=upgrade)
    monkeypatch.setattr(conftest.command, 'upgrade', migration)
    with monkeypatch.context() as fixture_environment:
        conftest.test_database.__wrapped__(fixture_environment)
        assert database_config.database_url().database == 'corpus_test'
    assert migration.call_count == 2
    assert engine_factory.call_args.args[0].database == 'corpus_test'
    assert os.environ['DATABASE_URL'] == RUNTIME_URL
    assert 'MIGRATION_DATABASE_URL' not in os.environ
    assert 'PYTHON_DOTENV_DISABLED' not in os.environ
    assert 'TEST_PRIVATE_MARKER' not in os.environ


def test_hrm_confirmation_must_come_from_process(monkeypatch, isolated_environment):
    isolated_environment.write_text('TEST_HRM_DISPOSABLE_CONFIRMED=true\n', encoding='utf-8')
    monkeypatch.setenv('TEST_HRM_DATABASE_URL', HRM_URL)
    with pytest.raises(pytest.fail.Exception, match='TEST_HRM_DISPOSABLE_CONFIRMED=true'):
        test_employee_source_integration.hrm_fixture_settings.__wrapped__()
    monkeypatch.setenv('TEST_HRM_DISPOSABLE_CONFIRMED', 'true')
    assert test_employee_source_integration.hrm_fixture_settings.__wrapped__().schema == 'public'
