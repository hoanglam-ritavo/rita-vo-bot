"""Explicit, development-only chat reset; never loads application environment files."""
import argparse
import asyncio
import json
import os
import re

BOT_TABLES = ('chat_messages', 'chat_turns', 'chat_conversations')
BACKEND_TABLES = ('chat_purge_jobs', 'chat_rooms')
CONFIRMATION = 'RESET_DEVELOPMENT_CHAT'


class ResetRejected(ValueError):
    """Fixed credential-safe rejection, distinct from unexpected library errors."""


def development_url(raw):
    from sqlalchemy.engine import make_url
    try:
        url = make_url(raw or '')
    except Exception:
        raise ResetRejected('Explicit development database URL required.') from None
    if (url.get_backend_name() != 'postgresql' or url.query or url.host not in ('localhost', '127.0.0.1', '::1')
            or not re.search(r'(?:^|[_-])(?:dev|development|test)(?:$|[_-])', url.database or '', re.I)):
        raise ResetRejected('Reset requires a local PostgreSQL database with a dev/development/test name segment.')
    return url.set(drivername='postgresql+psycopg')


async def reset_database(url, tables, *, apply=False):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine(url, echo=False, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            await connection.execute(text("SET LOCAL statement_timeout = '30s'"))
            external = (await connection.execute(text('''
                SELECT 1 FROM pg_constraint c
                JOIN pg_class parent ON parent.oid=c.confrelid
                JOIN pg_namespace pn ON pn.oid=parent.relnamespace
                JOIN pg_class child ON child.oid=c.conrelid
                JOIN pg_namespace cn ON cn.oid=child.relnamespace
                WHERE c.contype='f' AND pn.nspname='public'
                  AND parent.relname = ANY(:tables)
                  AND NOT (cn.nspname='public' AND child.relname = ANY(:tables))
                LIMIT 1
            '''), {'tables': list(tables)})).first()
            if external:
                raise ResetRejected('Unexpected external chat-table dependency; reset refused.')
            # Fixed allowlisted table names; never CASCADE or schema changes.
            counts = {}
            for table in tables:
                counts[table] = (await connection.execute(text(f'SELECT count(*) FROM public.{table}'))).scalar_one()
            if apply:
                for table in tables:
                    await connection.execute(text(f'DELETE FROM public.{table}'))
            return counts
    finally:
        await engine.dispose()


async def execute(args, environment):
    if args.apply and (args.confirm != CONFIRMATION or not args.services_stopped):
        raise ResetRejected('Apply requires the exact confirmation and --services-stopped.')
    bot = development_url(environment.get('CHAT_RESET_BOT_DATABASE_URL'))
    backend = development_url(environment.get('CHAT_RESET_BACKEND_DATABASE_URL'))
    # Both schemas must preflight successfully before either database is mutated.
    before = {'bot': await reset_database(bot, BOT_TABLES),
              'backend': await reset_database(backend, BACKEND_TABLES)}
    print(json.dumps({'mode': 'apply' if args.apply else 'dry_run', 'counts': before}))
    if args.apply:
        for label, url, tables in (('bot', bot, BOT_TABLES), ('backend', backend, BACKEND_TABLES)):
            await reset_database(url, tables, apply=True)
            print(json.dumps({'database': label, 'chat_reset': 'committed'}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm')
    parser.add_argument('--services-stopped', action='store_true')
    args = parser.parse_args()
    try:
        # No .env import and no application configuration/bootstrap.
        from src.database.runtime import run_async
        run_async(execute(args, os.environ))
    except ResetRejected as error:
        print(str(error))
        return 2
    except Exception:
        print('Chat reset failed; inspect the per-database commit markers. No credentials or row content are reported.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
