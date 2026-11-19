"""Shared database and corpus steps for Linux release preparation."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from src.api.config import load_settings
from src.database import models  # noqa: F401 -- register bot tables
from src.database.config import database_url
from src.database.models import Document, DocumentChunk
from src.database import Base
from src.ingestion.corpus_manifest import read as read_manifest


class ReleaseError(Exception):
    """A diagnostic safe to show to release operators."""


ROUTING_KEYS = {'host', 'hostaddr', 'port', 'dbname', 'service', 'servicefile', 'user'}


def target(url):
    if (',' in url.host or ROUTING_KEYS.intersection(key.casefold() for key in url.query)
            or any(os.environ.get(key) for key in ('PGHOSTADDR', 'PGSERVICE'))):
        raise ReleaseError('Use direct PostgreSQL host, port and database settings for release checks.')
    try:
        port = url.port or int(os.environ.get('PGPORT') or 5432)
    except ValueError:
        raise ReleaseError('PostgreSQL port must be a number.') from None
    if not 1 <= port <= 65535:
        raise ReleaseError('PostgreSQL port is out of range.')
    return url.host.casefold(), port, url.database


def effective_configuration():
    inherited = os.environ.copy()
    try:
        runtime_url = database_url()
        migration_url = database_url(migration=True)
        os.environ.clear()
        os.environ.update(inherited)
        settings = load_settings()
        http_url = database_url()
    finally:
        os.environ.clear()
        os.environ.update(inherited)
    if len({target(url) for url in (runtime_url, migration_url, http_url)}) != 1:
        raise ReleaseError('Migration, publication and HTTP must target the same database.')
    return runtime_url, migration_url, settings


def migration_needed(root, url, *, first):
    scripts = ScriptDirectory.from_config(Config(str(root / 'alembic.ini')))
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise ReleaseError('Expected one Alembic head in the release.')
    head, = heads
    engine = create_engine(url, hide_parameters=True, connect_args={'connect_timeout': 10})
    try:
        with engine.connect() as connection:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            if not connection.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")):
                raise ReleaseError('Have the DBA enable vector in the target database first.')
            schemas = inspect(connection)
            current_schema = schemas.default_schema_name
            tables = set(schemas.get_table_names())
            if first:
                bot_tables = {table.name for table in Base.metadata.tables.values()} | {'alembic_version'}
                for schema in schemas.get_schema_names():
                    if schema != current_schema and schema != 'information_schema' and not schema.startswith('pg_'):
                        tables.update(schemas.get_table_names(schema=schema))
                if tables & bot_tables:
                    raise ReleaseError('First release requires a database without bot tables or Alembic history.')
                return True
            if not {'alembic_version', Document.__tablename__, DocumentChunk.__tablename__} <= tables:
                raise ReleaseError('Update requires an initialized bot database.')
            current = tuple(connection.scalars(text('SELECT version_num FROM alembic_version')))
    finally:
        engine.dispose()
    supported = {revision.revision for revision in scripts.walk_revisions()}
    if len(current) != 1 or current[0] not in supported:
        raise ReleaseError('Database revision is outside the release migration chain.')
    return current[0] != head


def command(root, args, *, environment=None, capture=False):
    result = subprocess.run(args, cwd=root, env=environment, text=True,
                            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, check=False)
    return result


def backup(root, output, url):
    # A private libpq service file keeps connection settings out of process arguments.
    options = dict(url.translate_connect_args(username='user', database='dbname'))
    options.update(url.query)
    options['port'] = target(url)[1]
    password = options.pop('password', None)
    service = output / 'backup-connection.conf'
    archive = output / 'database.dump'
    lines = ['[rita_release_backup]']
    for key, value in options.items():
        if not re.fullmatch('[A-Za-z0-9_]+', key) or not isinstance(value, (str, int)) or any(
                ch in str(value) for ch in '\r\n'):
            raise ReleaseError('Backup requires single-valued PostgreSQL connection settings.')
        lines.append(f'{key}={value}')
    descriptor = os.open(service, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')
        environment = dict(os.environ, PGSERVICEFILE=str(service))
        if password is not None:
            environment['PGPASSWORD'] = password
        result = command(root, ['pg_dump', '--no-password', '--dbname=service=rita_release_backup',
                                '--format=custom', '--file', str(archive)], environment=environment)
        if result.returncode or not archive.is_file() or archive.stat().st_size == 0:
            raise ReleaseError('Database backup failed.')
    finally:
        service.unlink()


def record(output, stage, status):
    with (output / 'steps.jsonl').open('a', encoding='utf-8') as log:
        log.write(json.dumps({'stage': stage, 'status': status}) + '\n')


def apply_release(root, output, *, first):
    stage = 'configuration'
    migrated = False
    try:
        runtime_url, migration_url, settings = effective_configuration()
        environment = dict(os.environ, PYTHON_DOTENV_DISABLED='1',
                           DATABASE_URL=runtime_url.render_as_string(hide_password=False),
                           MIGRATION_DATABASE_URL=migration_url.render_as_string(hide_password=False))
        record(output, stage, 'passed')
        for module, arguments in (('src.cli.build_artifacts', ['check']),
                                  ('src.cli.check_answer_catalog', []), ('scripts.verify_runtime_assets', [])):
            stage = module
            if command(root, [sys.executable, '-m', module, *arguments]).returncode:
                raise ReleaseError('Release prerequisite check failed.')
            record(output, stage, 'passed')
        stage = 'manifest'
        entries, digest = read_manifest(root)
        record(output, stage, 'passed')
        corpus_options = ['--model-path', str(settings.model_path), '--model-revision', settings.model_revision,
                          '--expected-manifest-sha256', digest]
        stage = 'corpus_preflight'
        preview = command(root, [sys.executable, '-m', 'src.cli.publish_corpus', 'plan', *corpus_options],
                          capture=True)
        plan = json.loads(preview.stdout)
        (output / 'corpus-plan.json').write_text(json.dumps(plan, indent=2) + '\n', encoding='utf-8')
        if preview.returncode or plan.get('status') != 'planned':
            raise ReleaseError('Corpus preflight failed. Review corpus-plan.json.')
        record(output, stage, 'passed')
        stage = 'database'
        upgrade = migration_needed(root, migration_url, first=first)
        record(output, stage, 'passed')
        if not first:
            stage = 'backup'
            backup(root, output, migration_url)
            record(output, stage, 'passed')
        migration_steps = [('alembic', 'upgrade', 'head')] if upgrade else []
        migration_steps += [('alembic', 'current'), ('alembic', 'check'), ('src.cli.check_db',)]
        for step in migration_steps:
            stage = ' '.join(step)
            if command(root, [sys.executable, '-m', *step], environment=environment).returncode:
                raise ReleaseError('Schema operation failed. Inspect the database before retrying.')
            migrated = migrated or step[1:2] == ('upgrade',)
            record(output, stage, 'passed')
        stage = 'publication'
        result = command(root, [sys.executable, '-m', 'src.cli.publish_corpus', 'apply', *corpus_options],
                         environment=environment, capture=True)
        report = json.loads(result.stdout)
        (output / 'publication.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        published = [(item['document_id'], item['revision_id']) for item in report['results']
                     if item['phase'] == 'publish' and item['status'] in ('processed', 'skipped')
                     and (item['code'] == 'published' or item['status'] == 'skipped')]
        expected = sorted((entry['document_id'], entry['revision_id']) for entry in entries)
        if result.returncode or report['status'] != 'completed' or report['unattempted'] or (
                report.get('manifest_sha256') != digest or sorted(published) != expected):
            if report.get('code') == 'publication_outcome_unknown':
                for item in report['results']:
                    if item['code'] == 'publication_outcome_unknown' and item.get('attempt_id'):
                        print(f"Inspect publication attempt {item['attempt_id']} before retrying.", file=sys.stderr)
            raise ReleaseError('Publication did not complete. Review publication.json.')
        record(output, stage, 'passed')
    except Exception as exc:
        record(output, stage, 'failed')
        detail = str(exc) if isinstance(exc, ReleaseError) else 'Check the release configuration and reports.'
        print(f'Release stopped during {stage}. {detail}', file=sys.stderr)
        if first and migrated:
            print('Verify the migrated schema and use the update flow after resolving the error.', file=sys.stderr)
        return 1
    print('Ready for service handoff. Start from the release checkout with:')
    print('./env/bin/python -m src.cli.serve_api')
    print('Check liveness, authenticated readiness and a source-backed question before opening traffic.')
    return 0
