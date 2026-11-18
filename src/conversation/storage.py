"""Short transactions serialized by room, including absent-room and purge races."""
import hashlib
import json
from sqlalchemy import select, text, update, delete
from src.database import session_scope
from src.database.models.conversation import Conversation, ChatTurn, ChatMessage
from src.api.contracts import ApiError
from .contracts import TurnResponse


def fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def response(turn):
    return TurnResponse(room_id=turn.room_id, message_id=turn.message_id, sequence=turn.sequence,
                        state=turn.state, attempt=turn.attempt, context_version=turn.context_version,
                        result=turn.result, error_code=turn.error_code)


async def lock_room(db, room_id):
    await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:room, 0))'), {'room': room_id})


async def room(db, room_id, owner_id, *, create=False):
    await lock_room(db, room_id)
    row = await db.get(Conversation, room_id)
    if row is None and create:
        row = Conversation(room_id=room_id, owner_id=owner_id, deleted=False, version=0,
                           sequence=0, checkpoint_version=0, checkpoint={})
        db.add(row)
        await db.flush()
    if row is None or row.deleted or row.owner_id != owner_id:
        raise ApiError('room_unavailable', 404)
    return row


class ConversationStore:
    async def recover(self):
        async with session_scope() as db:
            await db.execute(update(ChatTurn).where(ChatTurn.state == 'PROCESSING').values(
                state='INTERRUPTED', error_code='process_interrupted'))

    async def begin(self, request, *, retry=False):
        payload = request.model_dump()
        async with session_scope() as db:
            current = await room(db, request.room_id, request.user_id, create=True)
            turn = await db.get(ChatTurn, (request.room_id, request.message_id))
            if turn is not None:
                if turn.fingerprint != fingerprint(payload):
                    raise ApiError('message_conflict', 409)
                if not retry or turn.state in ('PROCESSING', 'COMPLETED'):
                    return response(turn), False
                if turn.sequence != current.sequence or turn.context_version != current.version:
                    raise ApiError('retry_context_conflict', 409)
                turn.attempt += 1
                turn.state, turn.error_code = 'PROCESSING', None
            else:
                if retry:
                    raise ApiError('turn_unavailable', 404)
                if request.expected_context_version != current.version:
                    raise ApiError('context_conflict', 409)
                active = await db.scalar(select(ChatTurn.message_id).where(
                    ChatTurn.room_id == request.room_id, ChatTurn.state == 'PROCESSING').limit(1))
                if active:
                    raise ApiError('busy', 429)
                current.sequence += 1
                turn = ChatTurn(room_id=request.room_id, message_id=request.message_id,
                    sequence=current.sequence, fingerprint=fingerprint(payload), payload=payload,
                    state='PROCESSING', attempt=1, context_version=current.version)
                db.add(turn)
                await db.flush()
                db.add(ChatMessage(room_id=request.room_id, message_id=request.message_id,
                                   role='user', content=request.text))
            await db.flush()
            return response(turn), True

    async def context(self, room_id, owner_id):
        async with session_scope() as db:
            current = await room(db, room_id, owner_id)
            deltas = list(await db.scalars(select(ChatTurn.context_delta).where(
                ChatTurn.room_id == room_id, ChatTurn.state == 'COMPLETED',
                ChatTurn.context_version > current.checkpoint_version).order_by(ChatTurn.context_version)))
            return current.version, current.checkpoint, deltas

    async def version(self, room_id, owner_id):
        async with session_scope() as db:
            return (await room(db, room_id, owner_id)).version

    async def finish(self, request, attempt, result, delta):
        async with session_scope() as db:
            current = await room(db, request.room_id, request.user_id)
            turn = await db.get(ChatTurn, (request.room_id, request.message_id))
            if turn is None or turn.state != 'PROCESSING' or turn.attempt != attempt or current.version != turn.context_version:
                raise ApiError('turn_conflict', 409)
            current.version += 1
            turn.state, turn.result, turn.context_delta = 'COMPLETED', result, delta
            turn.context_version = current.version
            db.add(ChatMessage(room_id=request.room_id, message_id=request.message_id,
                               role='assistant', content=result['answer']))
            if current.version % 8 == 0:
                current.checkpoint, current.checkpoint_version = delta, current.version
            await db.flush()
            return response(turn)

    async def fail(self, request, attempt, state, code):
        async with session_scope() as db:
            await lock_room(db, request.room_id)
            turn = await db.get(ChatTurn, (request.room_id, request.message_id))
            if turn is not None and turn.state == 'PROCESSING' and turn.attempt == attempt:
                turn.state, turn.error_code = state, code

    async def get_turn(self, room_id, owner_id, message_id, *, payload=False):
        async with session_scope() as db:
            await room(db, room_id, owner_id)
            turn = await db.get(ChatTurn, (room_id, message_id))
            if turn is None:
                raise ApiError('turn_unavailable', 404)
            return turn.payload if payload else response(turn)

    async def messages(self, room_id, owner_id, after=0, limit=20):
        async with session_scope() as db:
            current = await room(db, room_id, owner_id, create=True)
            rows = list(await db.scalars(select(ChatTurn).where(ChatTurn.room_id == room_id,
                ChatTurn.sequence > after).order_by(ChatTurn.sequence).limit(limit + 1)))
            items, used = [], 0
            for turn in rows[:limit]:
                item = response(turn).model_dump() | {'text': turn.payload['text']}
                size = len(json.dumps(item, ensure_ascii=False).encode())
                if used + size > 1_900_000:
                    break
                items.append(item)
                used += size
            return {'items': items, 'next_cursor': items[-1]['sequence'] if items and len(items) < len(rows) else None,
                    'context_version': current.version}

    async def purge(self, room_id, owner_id):
        async with session_scope() as db:
            await lock_room(db, room_id)
            current = await db.get(Conversation, room_id)
            if current is None:
                current = Conversation(room_id=room_id, owner_id=None, deleted=True)
                db.add(current)
            else:
                if not current.deleted and current.owner_id != owner_id:
                    raise ApiError('room_unavailable', 404)
                current.deleted, current.owner_id = True, None
                current.checkpoint, current.checkpoint_version = {}, 0
                current.version, current.sequence = 0, 0
            await db.execute(delete(ChatTurn).where(ChatTurn.room_id == room_id))
