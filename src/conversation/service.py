import asyncio
import json
import logging
import time
from src.api.admission import try_admit, release
from src.api.contracts import ApiError
from src.api.answer import drain
from .contracts import ConversationRequest
from .context import plan_turn, compact, identity, current_choice, published_options

logger = logging.getLogger(__name__)
NAMESPACE = 'conversation-context-v2'
DEADLINE_SECONDS = 120


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
        self.legacy_identity_stamp = None

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
        started = time.perf_counter()
        version = await self.store.version(request.room_id, request.user_id)
        cached = await self.cached('get', self.key(request.room_id, request.user_id))
        if isinstance(cached, dict) and cached.get('version') == version and isinstance(cached.get('state'), dict):
            logger.info('conversation_cache state=hit elapsed_ms=%.1f', (time.perf_counter() - started) * 1000)
            return cached['state']
        _, state, deltas = await self.store.context(request.room_id, request.user_id)
        for delta in deltas:
            state = delta
        logger.info('conversation_cache state=rebuild snapshots=%d elapsed_ms=%.1f',
                    len(deltas), (time.perf_counter() - started) * 1000)
        return state

    def catalog_identity(self):
        if self.identity_stamp is None:
            policy = getattr(getattr(self.owner, 'retrieval', None), 'policy', None)
            catalog, lexicon = self.owner.answers.catalog, self.owner.answers.lexicon
            self.identity_stamp = identity(catalog, lexicon, policy)
            self.legacy_identity_stamp = identity(catalog, lexicon, policy, schema=7)
        return self.identity_stamp

    def public_result(self, result):
        if result is None or 'clarification_options' not in result:
            return result
        result = dict(result)
        if result.pop('clarification_catalog', None) != self.catalog_identity():
            result.pop('clarification_options')
        return result

    def public_turn(self, turn):
        return turn.model_copy(update={'result': self.public_result(turn.result)})

    async def turn(self, room_id, owner_id, message_id):
        return self.public_turn(await self.store.get_turn(room_id, owner_id, message_id))

    async def messages(self, room_id, owner_id, after, limit):
        page = await self.store.messages(room_id, owner_id, after, limit)
        return {**page, 'items': [
            {**turn, 'result': self.public_result(turn['result'])} for turn in page['items']]}

    async def answer(self, request, *, retry=False):
        try:
            async with asyncio.timeout(DEADLINE_SECONDS) as deadline:
                return await self._answer(request, retry=retry, deadline=deadline)
        except TimeoutError:
            logger.warning('conversation_deadline state=expired')
            raise ApiError('answer_timeout', 504) from None

    async def _answer(self, request, *, retry=False, deadline=None):
        if self.stopping:
            raise ApiError('conversation_unavailable')
        # Standalone inference and conversations reserve the same slot before I/O.
        if not try_admit(self.owner):
            existing = None
            try:
                existing = await self.store.get_turn(request.room_id, request.user_id, request.message_id,
                                                     payload=True)
            except ApiError as error:
                if error.status_code != 404:
                    raise
            if existing == request.stored_payload():
                return await self.turn(request.room_id, request.user_id, request.message_id)
            if existing is not None:
                raise ApiError('message_conflict', 409)
            raise ApiError('busy', 429)
        self.active = True
        self.task = asyncio.current_task()
        accepted = False
        turn = None
        stage = 'reservation'
        try:
            policy = getattr(getattr(self.owner, 'retrieval', None), 'policy', None)
            catalog, lexicon = self.owner.answers.catalog, self.owner.answers.lexicon
            stamp = self.catalog_identity()
            state, selected = None, None

            def validate_choice(snapshot):
                nonlocal state, selected
                state = snapshot
                selected = current_choice(state, request.choice, request.text, stamp)
                return selected is not None

            if request.choice is None:
                turn, accepted = await self.store.begin(request, retry=retry)
            else:
                turn, accepted = await self.store.begin(request, retry=retry, choice_is_current=validate_choice)
            if not accepted:
                return self.public_turn(turn)
            stage = 'context'
            if state is None:
                state = await self.context(request)
            analysis, delta = plan_turn(request.text, state, catalog, lexicon,
                identity_stamp=stamp, legacy_identity_stamp=self.legacy_identity_stamp,
                policy=policy, selected_task=selected)
            # The shared HTTP boundary already authenticated the service caller.
            stage = 'answer'
            result = await self.owner.context.execute(request.text, request.top_k,
                request.hrm_user_id,
                answer=True, analysis=analysis, reserved=True)
            value = result.model_dump(mode='json')
            options = published_options(analysis, delta, request.message_id, value)
            if options:
                value['clarification_options'] = options
                value['clarification_catalog'] = stamp
            if len(json.dumps(value, ensure_ascii=False).encode()) > 1_800_000:
                raise ApiError('answer_response_too_large')
            delta = compact(delta, value, analysis=analysis)
            stage = 'commit'
            turn = await self.store.finish(request, turn.attempt, value, delta)
            stage = 'post_commit'
            await self.cached('set', self.key(request.room_id, request.user_id),
                              {'version': turn.context_version, 'state': delta})
            try:
                await self.store.version(request.room_id, request.user_id)
            except ApiError:
                await self.cached('delete', self.key(request.room_id, request.user_id))
                raise
            return self.public_turn(turn)
        except BaseException as error:
            if deadline is not None and deadline.expired():
                logger.warning('conversation_deadline stage=%s', stage)
            if accepted and turn is not None:
                timed_out = deadline is not None and deadline.expired()
                state = 'CANCELLED' if isinstance(error, asyncio.CancelledError) and not timed_out else 'FAILED'
                code = 'answer_timeout' if timed_out else error.code if isinstance(error, ApiError) else 'conversation_failed'
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
            release(self.owner)

    async def retry(self, room_id, message_id, identity):
        try:
            async with asyncio.timeout(DEADLINE_SECONDS) as deadline:
                return await self._retry(room_id, message_id, identity, deadline)
        except TimeoutError:
            raise ApiError('answer_timeout', 504) from None

    async def _retry(self, room_id, message_id, identity, deadline):
        payload = await self.store.get_turn(room_id, identity.user_id, message_id, payload=True)
        if payload['hrm_user_id'] != identity.hrm_user_id:
            raise ApiError('identity_context_conflict', 409)
        return await self._answer(ConversationRequest.model_validate(payload), retry=True, deadline=deadline)

    async def purge(self, room_id, user_id):
        await self.store.purge(room_id, user_id)
        await self.cached('delete', self.key(room_id, user_id))
