import asyncio
import json
import logging
from src.api.contracts import ApiError
from src.api.answer import drain
from .contracts import ConversationRequest
from .context import reduce_question, compact, identity

logger = logging.getLogger(__name__)
NAMESPACE = 'conversation-context-v2'


class ConversationService:
    def __init__(self, owner, cache, store=None):
        self.owner, self.cache = owner, cache
        if store is None:
            from .storage import ConversationStore
            store = ConversationStore()
        self.store = store
        self.settings = owner.settings.conversation
        self.active = False
        self.stopping = False
        self.task = None
        self.identity_stamp = None

    def require_enabled(self):
        self.settings.require_enabled()

    async def start(self):
        if self.settings.enabled:
            await self.store.recover()

    async def close(self):
        self.stopping = True
        if self.task is not None and self.task is not asyncio.current_task():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    @staticmethod
    def key(room_id, owner_id):
        return f'{owner_id}:{room_id}'

    async def cached(self, method, *args, **kwargs):
        try:
            return await getattr(self.cache, method)(*args, namespace=NAMESPACE, **kwargs)
        except Exception:
            logger.warning('conversation_cache state=unavailable')
            return None

    async def context(self, request):
        version = await self.store.version(request.room_id, request.user_id)
        cached = await self.cached('get', self.key(request.room_id, request.user_id))
        if isinstance(cached, dict) and cached.get('version') == version and isinstance(cached.get('state'), dict):
            logger.info('conversation_cache state=hit')
            return cached['state']
        _, state, deltas = await self.store.context(request.room_id, request.user_id)
        for delta in deltas:
            state = delta
        logger.info('conversation_cache state=rebuild replay_count=%d', len(deltas))
        return state

    async def answer(self, request, *, retry=False):
        if self.stopping:
            raise ApiError('conversation_unavailable')
        # The gate is synchronous on the owning event loop. It covers durable
        # reservation/finalization; the existing engine still owns inference.
        if self.active or self.owner.busy:
            existing = None
            try:
                existing = await self.store.get_turn(request.room_id, request.user_id, request.message_id,
                                                     payload=True)
            except ApiError as error:
                if error.status_code != 404:
                    raise
            if existing == request.model_dump():
                return await self.store.get_turn(request.room_id, request.user_id, request.message_id)
            if existing is not None:
                raise ApiError('message_conflict', 409)
            raise ApiError('busy', 429)
        self.active = True
        self.task = asyncio.current_task()
        accepted = False
        turn = None
        try:
            turn, accepted = await self.store.begin(request, retry=retry)
            if not accepted:
                return turn
            state = await self.context(request)
            policy = getattr(getattr(self.owner, 'retrieval', None), 'policy', None)
            if self.identity_stamp is None:
                self.identity_stamp = identity(self.owner.answers.catalog, self.owner.answers.lexicon, policy)
            question, delta = reduce_question(request.text, state,
                self.owner.answers.catalog, self.owner.answers.lexicon, identity_stamp=self.identity_stamp, policy=policy)
            # The shared HTTP boundary already authenticated the service caller.
            result = await self.owner.context.execute(question, request.top_k,
                request.hrm_user_id,
                answer=True)
            value = result.model_dump(mode='json')
            if len(json.dumps(value, ensure_ascii=False).encode()) > 1_800_000:
                raise ApiError('answer_response_too_large')
            delta = compact(delta, value)
            turn = await self.store.finish(request, turn.attempt, value, delta)
            await self.cached('set', self.key(request.room_id, request.user_id),
                              {'version': turn.context_version, 'state': delta})
            try:
                await self.store.version(request.room_id, request.user_id)
            except ApiError:
                await self.cached('delete', self.key(request.room_id, request.user_id))
                raise
            return turn
        except BaseException as error:
            if accepted and turn is not None:
                state = 'CANCELLED' if isinstance(error, asyncio.CancelledError) else 'FAILED'
                code = error.code if isinstance(error, ApiError) else 'conversation_failed'
                try:
                    await drain(asyncio.create_task(self.store.fail(request, turn.attempt, state, code)))
                except Exception:
                    logger.warning('conversation_finalization state=uncertain')
            if isinstance(error, (ApiError, asyncio.CancelledError)):
                raise
            raise ApiError('conversation_unavailable') from None
        finally:
            self.active = False
            self.task = None

    async def retry(self, room_id, message_id, identity):
        payload = await self.store.get_turn(room_id, identity.user_id, message_id, payload=True)
        if payload['hrm_user_id'] != identity.hrm_user_id:
            raise ApiError('identity_context_conflict', 409)
        return await self.answer(ConversationRequest.model_validate(payload), retry=True)

    async def purge(self, room_id, user_id):
        await self.store.purge(room_id, user_id)
        await self.cached('delete', self.key(room_id, user_id))
