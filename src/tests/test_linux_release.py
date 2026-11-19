"""Release failure gates without a deployment host."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy.engine import make_url

from src import release_workflow as release
from src.api import config as http_config
from src.database import config as db_config


URL = make_url('postgresql+psycopg://bot:fixture-secret@127.0.0.1/bot')
DIGEST = 'a' * 64
ENTRY = {'document_id': 'fixture-document', 'revision_id': 'fixture-revision'}
SETTINGS = SimpleNamespace(model_path='model', model_revision='b' * 40)


def test_configuration_detects_http_target_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(db_config, 'ROOT', tmp_path)
    monkeypatch.setattr(http_config, 'REPOSITORY_ROOT', tmp_path)
    monkeypatch.delenv('PYTHON_DOTENV_DISABLED', raising=False)
    monkeypatch.delenv('MIGRATION_DATABASE_URL', raising=False)
    monkeypatch.setenv('DATABASE_URL', URL.render_as_string(hide_password=False))
    monkeypatch.setenv('CHAT_BOT_SERVICE_KEY', 'fixture-key')
    (tmp_path / '.env').write_text('DATABASE_URL=postgresql+psycopg://bot@127.0.0.1/other\n')
    with pytest.raises(release.ReleaseError, match='same database'):
        release.effective_configuration()


def test_missing_vector_stops_database_inspection(monkeypatch, tmp_path):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.scalar.return_value = None
    monkeypatch.setattr(release, 'create_engine', lambda *_args, **_kwargs: engine)
    script = Mock()
    script.get_heads.return_value = ['head']
    monkeypatch.setattr(release.ScriptDirectory, 'from_config', lambda _config: script)
    with pytest.raises(release.ReleaseError, match='enable vector'):
        release.migration_needed(tmp_path, URL, first=True)
    engine.dispose.assert_called_once()


def test_pg_dump_uses_private_connection_file(monkeypatch, tmp_path):
    monkeypatch.setenv('PGPORT', '55432')

    def dump(_root, args, *, environment):
        assert 'fixture-secret' not in ' '.join(args)
        assert environment['PGPASSWORD'] == 'fixture-secret'
        config = Path(environment['PGSERVICEFILE'])
        assert 'dbname=bot\n' in config.read_text(encoding='utf-8')
        assert 'port=55432\n' in config.read_text(encoding='utf-8')
        Path(args[-1]).write_bytes(b'PGDMP')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(release, 'command', dump)
    release.backup(tmp_path, tmp_path, URL)
    assert (tmp_path / 'database.dump').is_file()
    assert not (tmp_path / 'backup-connection.conf').exists()


@pytest.mark.parametrize(('first', 'pending', 'failure'), [
    (True, True, None), (False, False, None), (False, True, None),
    (False, True, 'backup'), (True, True, 'preflight'), (True, True, 'uncertain'),
])
def test_release_steps_and_failure_gates(tmp_path, monkeypatch, capsys, first, pending, failure):
    calls = []
    monkeypatch.setattr(release, 'effective_configuration', lambda: (URL, URL, SETTINGS))
    monkeypatch.setattr(release, 'read_manifest', lambda _root: ([ENTRY], DIGEST))
    monkeypatch.setattr(release, 'migration_needed', lambda *_args, **_kwargs: pending)

    def backup(*_args):
        calls.append('backup')
        if failure == 'backup':
            raise release.ReleaseError('Database backup failed.')

    def command(_root, args, *, environment=None, capture=False):
        name = ' '.join(args[2:])
        calls.append(name)
        report = None
        if args[2:4] == ['src.cli.publish_corpus', 'plan']:
            report = {'status': 'rejected' if failure == 'preflight' else 'planned'}
        if args[2:4] == ['src.cli.publish_corpus', 'apply']:
            assert environment['PYTHON_DOTENV_DISABLED'] == '1'
            assert args[-2:] == ['--expected-manifest-sha256', DIGEST]
            item = dict(ENTRY, phase='publish', status='processed' if first else 'skipped',
                        code='published' if first else 'identical_active_run')
            report = {'status': 'completed', 'unattempted': [], 'manifest_sha256': DIGEST, 'results': [item]}
            if failure == 'uncertain':
                item.update(code='publication_outcome_unknown', status='failed', attempt_id='fixture-attempt')
                report.update(status='failed', code='publication_outcome_unknown')
        return SimpleNamespace(returncode=0, stdout=json.dumps(report))

    monkeypatch.setattr(release, 'backup', backup)
    monkeypatch.setattr(release, 'command', command)
    assert release.apply_release(tmp_path, tmp_path, first=first) == (failure is not None)
    assert ('alembic upgrade head' in calls) == (pending and failure not in ('backup', 'preflight'))
    publishing = failure not in ('backup', 'preflight')
    assert any(call.startswith('src.cli.publish_corpus apply') for call in calls) == publishing
    if publishing:
        assert calls[-2] == 'src.cli.check_db'
    if not first and pending:
        stage = calls.index('backup')
        assert failure == 'backup' or calls[stage + 1] == 'alembic upgrade head'
    output = capsys.readouterr()
    assert ('Ready for service handoff' in output.out) == (failure is None)
    if failure == 'uncertain':
        assert 'fixture-attempt' in output.err and 'update flow' in output.err
