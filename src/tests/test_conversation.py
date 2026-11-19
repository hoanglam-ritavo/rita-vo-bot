"""Fictional context, orchestration and HTTP boundaries; no live model/source."""
from src.tests.http_fixtures import SERVICE_KEY, SERVICE_HEADERS, http_service_environment
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from src.api.app import create_app
from src.api.config import Settings
from src.api.contracts import ApiError
from src.conversation.contracts import ConversationRequest, ConversationSettings, RetryRequest, TurnResponse, from_environment
from src.conversation.context import reduce_question, compact, plan_turn
from src.conversation.service import ConversationService
from src.helper.cache import CacheManager
from pathlib import Path


def request(**changes):
    return ConversationRequest.model_validate(dict(user_id=str(uuid4()), room_id=str(uuid4()),
        message_id=str(uuid4()), hrm_user_id=7, text='fictional', expected_context_version=0, top_k=5) | changes)


def catalog():
    def card(key, label):
        return SimpleNamespace(id=key, label=label, aliases=[label], concept_ids=[], requires_any=[], excludes_any=[])
    cards = [card('birth', 'sinh con'), card('marriage', 'kết hôn')]
    return SimpleNamespace(cards=cards, model_dump=lambda: {'cards': [c.id for c in cards]})


def result():
    return {'status': 'needs_clarification', 'answer': 'Fictional clarification', 'claims': [],
            'sources': [], 'limitations': [], 'fallback_reason': 'needs_clarification',
            'relevance': {'status': 'unassessed', 'warnings': ['evidence_completeness_not_assessed']},
            'timings': {'retrieval_seconds': 0., 'generation_seconds': 0., 'verification_seconds': 0., 'total_seconds': 0.}}


def test_conservative_followups_clarification_and_topic_switch():
    cat, lex = catalog(), {'concepts': []}
    _, first = reduce_question('sinh con', {}, cat, lex)
    question, state = reduce_question('Còn hồ sơ thì sao?', first, cat, lex)
    assert question == 'sinh con' and state['request']['aspects'] == ['paperwork']
    assert state['task_id'] == 'birth'
    question, changed = reduce_question('kết hôn', state, cat, lex)
    assert question == 'kết hôn' and changed['task_id'] == 'marriage'
    for unsafe in ('tôi không sinh con', 'tôi có được hưởng không', 'hồ sơ của Lan', 'ngày đó là ngày nào'):
        question, _ = reduce_question(unsafe, first, cat, lex)
        assert question == unsafe
    _, ambiguous = reduce_question('sinh con và kết hôn', first, cat, lex)
    assert ambiguous['pending'] == 'task' and len(ambiguous['candidates']) == 2
    question, selected = reduce_question('kết hôn', ambiguous, cat, lex)
    assert selected['task_id'] == 'marriage' and question == 'sinh con và kết hôn'
    assert selected['request']['selected_task'] == 'marriage'
    changed_catalog = catalog()
    changed_catalog.model_dump = lambda: {'revision': 2}
    question, _ = reduce_question('hồ sơ', first, changed_catalog, lex)
    assert question == 'hồ sơ'


def test_context_does_not_promote_employee_facts_or_assistant_text():
    cat, lex = catalog(), {'concepts': []}
    _, state = reduce_question('phòng ban của tôi', {}, cat, lex)
    value = result() | {'application': {'state': 'applied'}, 'evidence': ['private']}
    state = compact(state, value)
    analysis, _ = plan_turn('còn quản lý trực tiếp thì sao?', state, cat, lex)
    assert analysis.direct_fields == ('manager_name',) and not analysis.clarification
    assert 'private' not in str(state) and 'Fictional clarification' not in str(state)
    _, unclear = reduce_question('phòng ban', {}, cat, lex)
    unclear = compact(unclear, result() | {'application': {'state': 'not_needed'}})
    question, _ = reduce_question('quản lý trực tiếp', unclear, cat, lex)
    assert question == 'quản lý trực tiếp'
    for _ in range(10):
        _, state = reduce_question('sinh con', state, cat, lex)
    assert len(state['recent']) == 4


def test_strict_contract_and_service_authentication():
    valid = request()
    for patch in ({'user_id': 'email@example.invalid'}, {'expected_context_version': True},
                  {'message_id': None}, {'text': 'x' * 2001}, {'text': ' '}):
        with pytest.raises(ValidationError):
            ConversationRequest.model_validate(valid.model_dump() | patch)
    settings = ConversationSettings(True)
    settings.require_enabled()
    with pytest.raises(ApiError) as error:
        ConversationSettings().require_enabled()
    assert error.value.status_code == 503
    assert from_environment({}).enabled is False
    assert from_environment({'CONVERSATION_ENABLED': 'true'}).enabled


def setup_service():
    settings = SimpleNamespace(conversation=ConversationSettings(True), type_b=SimpleNamespace(enabled=False))
    owner = SimpleNamespace(settings=settings, busy=False, _idle=asyncio.Event(), answers=SimpleNamespace(catalog=catalog(), lexicon={'concepts': []}),
                            context=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(model_dump=lambda **_: result()))))
    req = request()
    turn = TurnResponse(room_id=req.room_id, message_id=req.message_id, sequence=1,
                        state='PROCESSING', attempt=1, context_version=0)
    completed = turn.model_copy(update={'state': 'COMPLETED', 'context_version': 1, 'result': result()})
    store = AsyncMock()
    store.begin.return_value = (turn, True)
    store.version.side_effect = [0, 1]
    store.context.return_value = (0, {}, [])
    store.finish.return_value = completed
    return req, owner, store


def test_service_commits_before_cache_and_replays_without_inference():
    async def scenario():
        req, owner, store = setup_service()
        cache = CacheManager()
        svc = ConversationService(owner, cache, store)
        value = await svc.answer(req)
        assert value.state == 'COMPLETED'
        store.finish.assert_awaited_once()
        assert (await cache.get(svc.key(req.room_id, req.user_id), namespace='conversation-context-v2'))['version'] == 1
        store.begin.return_value = (value, False)
        assert await svc.answer(req) == value
        owner.context.execute.assert_awaited_once()
        await cache.close()
    asyncio.run(scenario())


def test_cache_replay_bypass_and_failed_cache_write():
    async def scenario():
        req, owner, store = setup_service()
        store.context.return_value = (0, {'old': True}, [{'latest': True}])
        cache = AsyncMock()
        cache.get.return_value = {'version': -1, 'state': {'stale': True}}
        cache.set.side_effect = ValueError('fictional oversized value')
        svc = ConversationService(owner, cache, store)
        assert await svc.context(req) == {'latest': True}
        store.version.side_effect = None
        store.version.return_value = 0
        assert (await svc.answer(req)).state == 'COMPLETED'
        store.fail.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', [ApiError('busy', 429), asyncio.CancelledError()])
def test_failure_and_disconnect_finalize_without_context_advance(failure):
    async def scenario():
        req, owner, store = setup_service()
        owner.context.execute.side_effect = failure
        cache = CacheManager()
        svc = ConversationService(owner, cache, store)
        with pytest.raises(type(failure)):
            await svc.answer(req)
        assert store.fail.await_args.args[2] == ('CANCELLED' if isinstance(failure, asyncio.CancelledError) else 'FAILED')
        store.finish.assert_not_awaited()
        assert not svc.active
        await cache.close()
    asyncio.run(scenario())


def test_busy_admission_precedes_reservation_and_allows_replay():
    async def scenario():
        from src.api.admission import admit, release
        req, owner, store = setup_service()
        cache = CacheManager()
        svc = ConversationService(owner, cache, store)
        admit(owner)
        store.get_turn.side_effect = ApiError('turn_unavailable', 404)
        with pytest.raises(ApiError) as error:
            await svc.answer(req)
        assert error.value.status_code == 429
        store.begin.assert_not_awaited()
        release(owner)

        async def begin(*args, **kwargs):
            with pytest.raises(ApiError) as busy:
                admit(owner)
            assert busy.value.status_code == 429
            return store.begin.return_value

        store.begin.side_effect = begin
        completed = await svc.answer(req)
        assert not owner.busy and owner._idle.is_set()
        admit(owner)
        store.get_turn.side_effect = [req.stored_payload(), completed]
        assert await svc.answer(req) == completed
        release(owner)
        await cache.close()
    asyncio.run(scenario())


def test_deleted_during_completion_evicts_late_cache():
    async def scenario():
        req, owner, store = setup_service()
        store.version.side_effect = [0, ApiError('room_unavailable', 404)]
        cache = CacheManager()
        svc = ConversationService(owner, cache, store)
        with pytest.raises(ApiError):
            await svc.answer(req)
        assert await cache.get(svc.key(req.room_id, req.user_id), namespace='conversation-context-v2') is None
        await cache.close()
    asyncio.run(scenario())


def test_http_requires_full_identity_key_and_preserves_stateless():
    settings = Settings(Path('unused'), 'a' * 40, conversation=ConversationSettings(True), service_key=SERVICE_KEY)
    fake = SimpleNamespace(settings=settings, start=AsyncMock(), close=AsyncMock())
    app = create_app(settings, service_factory=lambda _: fake)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ConversationService, 'start', AsyncMock())
        with TestClient(app) as client:
            svc = app.state.conversation_service
            req = request()
            svc.answer = AsyncMock(return_value=TurnResponse(room_id=req.room_id, message_id=req.message_id,
                sequence=1, state='PROCESSING', attempt=1, context_version=0))
            assert client.post('/api/v1/answer', json=req.model_dump()).status_code == 401
            assert client.post('/api/v1/answer', json={'hrm_user_id': 7, 'text': 'x', 'room_id': req.room_id}, headers=SERVICE_HEADERS).status_code == 422
            response = client.post('/api/v1/answer', json=req.model_dump(), headers=SERVICE_HEADERS)
            assert response.status_code == 202 and response.headers['cache-control'] == 'no-store'
            svc.answer.assert_awaited_once()
