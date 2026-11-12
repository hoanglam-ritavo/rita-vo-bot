"""Credential-safe migration diagnostics; no configuration or connection side effects."""
from enum import Enum
import re

import psycopg
from alembic.script.revision import RevisionError
from alembic.util.exc import CommandError, DatabaseNotAtHead
from sqlalchemy import exc as sa_exc


class Stage(Enum):
    CONFIGURATION = 'Configuration'
    ENGINE = 'Engine creation'
    CONNECTION = 'Database connection'
    EXECUTION = 'Alembic execution'
    CLEANUP = 'Engine cleanup'


MESSAGES = {
    'DB_AUTH': ('Database authentication failed.', 'Check migration-role credentials.'),
    'DB_CONNECT': ('The database connection failed.', 'Check connectivity, credentials and TLS settings.'),
    'DB_PERMISSION': ('The database role lacks permission for the requested operation.',
                      'Review migration-role privileges with the database administrator.'),
    'DB_UNAVAILABLE': ('The configured database does not exist.', 'Verify the intended database configuration.'),
    'DB_SCHEMA_CONFLICT': ('A required schema object is missing or already exists.',
                           'Compare the database schema and migration history before making changes.'),
    'DB_EXECUTION': ('A database operation failed.', 'Consult PostgreSQL server logs for the underlying cause.'),
    'ALEMBIC_NOT_AT_HEAD': ('The database is not at the migration head.',
                            'Inspect current and heads; review pending migrations before applying them.'),
    'ALEMBIC_REVISION': ('The migration revision history could not be resolved.',
                         'Compare current, heads and the available migration history.'),
    'ALEMBIC_COMMAND': ('The Alembic command could not complete.',
                        'Review command arguments and migration state.'),
    'MIGRATION_UNKNOWN': ('An unclassified migration error occurred.',
                          'Review the migration environment and server logs; no specific cause was identified.'),
}


def _exceptions(error):
    pending, seen = [error], set()
    while pending and len(seen) < 16:
        current = pending.pop(0)
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        # Explicit wrappers/causes take precedence over incidental context.
        pending.extend((getattr(current, 'orig', None), current.__cause__))
        if current.__cause__ is None and not current.__suppress_context__:
            pending.append(current.__context__)


def format_error(error: Exception, stage: Stage) -> str:
    """Render only fixed messages and a validated driver SQLSTATE, never exception text."""
    chain = list(_exceptions(error))
    state = next((value for item in chain if isinstance(item, psycopg.Error)
                  and isinstance(value := item.sqlstate, str)
                  and re.fullmatch(r'[0-9A-Z]{5}', value)), None)
    if state is not None:
        if state.startswith('28'):
            code = 'DB_AUTH'
        elif state.startswith('08'):
            code = 'DB_CONNECT'
        else:
            code = {
                '42501': 'DB_PERMISSION', '3D000': 'DB_UNAVAILABLE',
                **dict.fromkeys(('42P07', '42701', '42710', '42P01', '42703', '42704'),
                                'DB_SCHEMA_CONFLICT'),
            }.get(state, 'DB_EXECUTION')
    elif any(isinstance(item, DatabaseNotAtHead) or
             (type(item) is CommandError and item.args == ('Target database is not up to date.',))
             for item in chain):
        # Exact upstream message in pinned Alembic: autogenerate uses CommandError.
        code = 'ALEMBIC_NOT_AT_HEAD'
    elif any(isinstance(item, RevisionError) for item in chain):
        code = 'ALEMBIC_REVISION'
    elif any(isinstance(item, CommandError) for item in chain):
        code = 'ALEMBIC_COMMAND'
    elif stage is Stage.CONNECTION and any(isinstance(item, (
        psycopg.OperationalError, psycopg.InterfaceError, sa_exc.OperationalError,
        sa_exc.InterfaceError, sa_exc.TimeoutError, ConnectionError, TimeoutError,
    )) for item in chain):
        code = 'DB_CONNECT'
    elif any(isinstance(item, (psycopg.Error, sa_exc.DBAPIError)) for item in chain):
        code = 'DB_EXECUTION'
    else:
        code = 'MIGRATION_UNKNOWN'
    description, hint = MESSAGES[code]
    suffix = f' SQLSTATE={state}.' if state else ''
    return f'[{code}] {stage.value} failed.{suffix}\n{description}\n{hint}'
