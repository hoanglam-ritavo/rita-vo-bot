"""Inspect one publication attempt; optionally finalize an existing commit receipt."""
import argparse
import json
from uuid import UUID


async def inspect(attempt_id, reconcile=False):
    from sqlalchemy import select, text
    from src.database import session_scope, close_database
    from src.database.models import PublicationAttempt
    from src.ingestion import attempts
    try:
        if reconcile:
            await attempts.reconcile(attempt_id)
        async with session_scope() as db:
            await db.execute(text('SET TRANSACTION READ ONLY'))
            row = await db.scalar(select(PublicationAttempt).where(PublicationAttempt.id == attempt_id))
            if row is None:
                return {'code': 'attempt_not_found', 'attempt_id': str(attempt_id)}, 1
            result = {key: getattr(row, key) for key in (
                'id', 'declared_revision_id', 'status', 'outcome', 'error_code', 'run_id', 'started_at', 'finished_at')}
            return result, 0
    except Exception:
        return {'code': 'attempt_unavailable_or_unresolved', 'attempt_id': str(attempt_id)}, 2
    finally:
        await close_database()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', required=True, type=UUID)
    parser.add_argument('--reconcile', action='store_true',
                        help='Finalize a committed receipt only; never publish or retry.')
    args = parser.parse_args(argv)
    from src.database.runtime import run_async
    try:
        result, code = run_async(inspect(args.id, args.reconcile))
    except Exception:
        result, code = {'code': 'attempt_inspection_failed'}, 2
    print(json.dumps(result, default=str))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
