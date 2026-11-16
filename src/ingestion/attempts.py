"""Durable execution receipts; never retry an uncertain publication."""
import asyncio
from uuid import UUID, uuid4
from sqlalchemy import select, func
from src.database import session_scope
from src.database.models import PublicationAttempt


class AttemptUncertain(RuntimeError):
    def __init__(self, attempt_id):
        super().__init__('publication_outcome_unknown')
        self.attempt_id = str(attempt_id)


async def start(revision_id):
    attempt_id = uuid4()
    try:
        async with session_scope() as db:
            db.add(PublicationAttempt(id=attempt_id, declared_revision_id=UUID(revision_id), status='running'))
    except BaseException as exc:
        # Even starting tracking can have an uncertain commit; do not begin embedding.
        if isinstance(exc, asyncio.CancelledError):
            exc.attempt_id = str(attempt_id)
            raise
        raise AttemptUncertain(attempt_id) from None
    return attempt_id


async def receipt(db, attempt_id, run_id, outcome):
    attempt = await db.get(PublicationAttempt, attempt_id)
    attempt.run_id, attempt.outcome = UUID(str(run_id)), outcome


async def finish(attempt_id, *, error_code=None, cancelled=False):
    """A committed receipt takes precedence over an observed transport failure."""
    async with session_scope() as db:
        attempt = await db.scalar(select(PublicationAttempt).where(
            PublicationAttempt.id == attempt_id).with_for_update())
        if attempt is None:
            raise AttemptUncertain(attempt_id)
        if attempt.status != 'running':
            return attempt.status
        if attempt.outcome is not None:
            attempt.status = 'succeeded'
            attempt.error_code = None
        elif error_code is not None:
            attempt.status = 'cancelled' if cancelled else 'failed'
            attempt.error_code = error_code
        else:
            raise AttemptUncertain(attempt_id)
        attempt.finished_at = func.clock_timestamp()
        return attempt.status


async def reconcile(attempt_id):
    """Complete a committed receipt only; absence is not proof of a failed commit."""
    try:
        return await finish(attempt_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise AttemptUncertain(attempt_id) from None
