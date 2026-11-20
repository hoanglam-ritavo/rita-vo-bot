"""Actual HTTP/coordinator/reducer paths with fictional evidence and durable memory."""
from dataclasses import replace
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.contracts import ApiError
from src.answering.catalog import load_catalog
from src.answering.context_spec import ContextSpec
from src.answering.contextual import prepare, requirements
from src.conversation.contracts import ConversationSettings, TurnResponse
from src.conversation.context import plan_turn, compact
from src.paths import REPOSITORY_ROOT
from src.retrieval.policy import RetrievalPolicy
from src.tests.test_answering import sample, service
from src.tests.test_contextual_answering import snapshot
from src.tests.http_fixtures import SERVICE_HEADERS
from src.type_b.config import TypeBSettings
from src.type_b.employee_store.errors import EmployeeValidationError
from src.type_b.service import ContextCoordinator


@pytest.fixture
def routing():
    catalog, lexicon = load_catalog(REPOSITORY_ROOT/'artifacts/answers/catalog.json', REPOSITORY_ROOT/'artifacts/semantics/lexicon.json')
    policy = RetrievalPolicy()
    policy.start()
    assert policy.catalog is not None
    return catalog, lexicon, policy


class MemoryStore:
    """Fictional transaction boundary; never imports the DB engine or loads env."""
    def __init__(self):
        self.version_number, self.delta, self.turns, self.payloads = 0, {}, {}, {}

    async def recover(self):
        return 0

    async def begin(self, request, *, retry=False, choice_is_current=None):
        key = request.message_id
        if key in self.turns:
            if self.payloads[key] != request.stored_payload():
                raise ApiError('message_conflict', 409)
            return self.turns[key], False
        if request.expected_context_version != self.version_number:
            raise ApiError('context_conflict', 409)
        if request.choice and not (choice_is_current and choice_is_current(self.delta)):
            raise ApiError('context_conflict', 409)
        self.payloads[key] = deepcopy(request.stored_payload())
        self.turns[key] = TurnResponse(room_id=request.room_id, message_id=key, sequence=len(self.turns)+1,
            state='PROCESSING', attempt=1, context_version=self.version_number)
        return self.turns[key], True

    async def version(self, *args):
        return self.version_number

    async def context(self, room_id, owner_id, cached=None):
        if cached and cached['version'] == self.version_number:
            return cached['state'], 'hit'
        return deepcopy(self.delta), 'snapshot'

    async def finish(self, request, attempt, result, delta):
        self.version_number += 1
        self.delta = deepcopy(delta)
        value = self.turns[request.message_id].model_copy(update={
            'state':'COMPLETED', 'context_version':self.version_number, 'result':result})
        self.turns[request.message_id] = value
        return value

    async def get_turn(self, room_id, owner_id, message_id, *, payload=False):
        value = self.payloads.get(message_id) if payload else self.turns.get(message_id)
        if value is None:
            raise ApiError('turn_unavailable', 404)
        return value

    async def messages(self, room_id, owner_id, after, limit, *, order, before):
        turns = sorted(self.turns.values(), key=lambda turn: turn.sequence, reverse=order == 'desc')
        if order == 'desc':
            turns = [turn for turn in turns if before is None or turn.sequence < before]
        else:
            turns = [turn for turn in turns if turn.sequence > (after or 0)]
        items = [turn.model_dump() | {'text': self.payloads[turn.message_id]['text']} for turn in turns[:limit]]
        return {'items': items, 'next_cursor': items[-1]['sequence'] if len(turns) > limit else None,
                'context_version': self.version_number}, 0

    async def fail(self, *args):
        raise AssertionError('Unexpected failure in fictional conversation')


def test_http_correlates_conversation_receipt_and_employee_lookup_without_logging_payload(sample, monkeypatch, caplog):
    owner = service(sample)
    owner.settings = replace(owner.settings, conversation=ConversationSettings(True), type_b=TypeBSettings(True))
    job_title = 'private-test-job-title'
    employee = SimpleNamespace(read_context=AsyncMock(return_value=snapshot(job_title=job_title)),
                               check_source=AsyncMock(), close=AsyncMock())
    owner.context = ContextCoordinator(owner, employee)
    store = MemoryStore()
    monkeypatch.setattr('src.conversation.storage.ConversationStore', lambda: store)
    caplog.set_level('INFO', logger='src.api.app')
    caplog.set_level('INFO', logger='src.type_b.service')
    payload = dict(user_id=str(uuid4()), hrm_user_id=741852963, room_id=str(uuid4()), message_id=str(uuid4()),
                   expected_context_version=0, text='Chức danh hiện tại của tôi là gì?', top_k=5)

    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        assert client.post('/api/v1/answer', json=payload).status_code == 401
        invalid = client.post('/api/v1/answer', json=payload | {'hrm_user_id': 0}, headers=SERVICE_HEADERS)
        assert invalid.status_code == 422
        assert not any('conversation_answer_received' in message for message in caplog.messages)

        response = client.post('/api/v1/answer', json=payload, headers=SERVICE_HEADERS)
        assert response.status_code == 200
        assert response.json()['result']['application']['state'] == 'applied'
        assert job_title in response.json()['result']['answer']
        replay = client.post('/api/v1/answer', json=payload, headers=SERVICE_HEADERS)
        assert replay.json() == response.json()

    receipts = [message for message in caplog.messages if message.startswith('conversation_answer_received ')]
    assert receipts == [f"conversation_answer_received message_id={payload['message_id']} "
                        'expected_context_version=0 top_k=5 has_choice=False'] * 2
    (lookup,) = [message for message in caplog.messages if message.startswith('employee_context_lookup ')]
    assert f"message_id={payload['message_id']} attempt=1 stage=read_context state=applied" in lookup
    assert 'requested_fields=job_title missing_fields=-' in lookup
    employee.read_context.assert_awaited_once_with(payload['hrm_user_id'], ('job_title',), manager=False)
    for value in (*SERVICE_HEADERS.values(), job_title, payload['text'], payload['room_id'],
                  payload['user_id'], str(payload['hrm_user_id'])):
        assert value not in caplog.text


@pytest.mark.parametrize('ending', ['.', '?', '', '\n'])
@pytest.mark.parametrize('reply', ['Workit', 'Workit.', 'dùng workit', 'đang dùng Workit'])
def test_http_pending_platform_survives_punctuation_and_cache_rebuild(sample, routing, monkeypatch, ending, reply):
    catalog, lexicon, policy = routing
    sections = dict(sample.catalog.cards[0].sections)
    sections['procedure'] = sections['amount']
    # The topic/route registry is real; every answer unit is fictional.
    owner = service(sample)
    owner.answers.catalog = catalog.model_copy(update={'cards': [card.model_copy(update={
        'sections':sections, 'context':None}) for card in catalog.cards]})
    owner.answers.lexicon = lexicon
    owner.retrieval.policy = policy
    owner.settings = replace(owner.settings, conversation=ConversationSettings(True))
    store = MemoryStore()
    monkeypatch.setattr('src.conversation.storage.ConversationStore', lambda: store)
    room, actor = str(uuid4()), str(uuid4())
    def body(text):
        return dict(user_id=actor, hrm_user_id=7, room_id=room, message_id=str(uuid4()),
                    expected_context_version=store.version_number, text=text, top_k=5)
    with TestClient(create_app(owner.settings, service_factory=lambda _:owner), headers=SERVICE_HEADERS) as client:
        first = client.post('/api/v1/answer', json=body('Thao tác trên phần mềm để xin nghỉ phép 2 ngày cần làm gì'+ending))
        assert first.status_code == 200 and first.json()['result']['status'] == 'needs_clarification'
        assert store.delta['missing_slots'] == ['system']
        original = deepcopy(store.delta['request'])
        unclear = client.post('/api/v1/answer', json=body('tôi chưa biết'))
        assert unclear.json()['result']['status'] == 'needs_clarification'
        assert store.delta['request']['question'] == original['question']
        # A cache miss must reconstruct the exact pending request from storage.
        client.portal.call(lambda: client.app.state.cache_manager.clear(namespace='conversation-context-v2'))
        final_body = body(reply)
        final = client.post('/api/v1/answer', json=final_body)
        assert final.status_code == 200 and final.json()['result']['status'] == 'answered'
        assert store.delta['request']['question'] == original['question']
        assert store.delta['request']['slots']['system'] == 'workit'
        assert store.delta['requested_aspects'] == ['procedure']
        assert store.delta['pending'] is None
        assert owner.retrieval.read_evidence.await_count == 3
        again = client.post('/api/v1/answer', json=final_body)
        assert again.json()['result'] == final.json()['result']
        assert owner.retrieval.read_evidence.await_count == 3


def test_general_leave_does_not_require_platform(routing):
    cat, lex, policy = routing
    plan, _ = plan_turn('Tôi muốn xin nghỉ phép 2 ngày thì cần làm gì.', {}, cat, lex, policy=policy)
    assert plan.cards[0].id == 'task.leave.policy.annual'
    assert plan.aspects == ('procedure',) and not plan.clarification


def test_choice_keeps_personal_subject_dates_and_only_new_aspect(routing):
    cat, lex, policy = routing
    original = 'Hồ sơ hưởng chế độ khi sinh con của tôi; Ngày xét: 2026-11-18'
    plan, state = plan_turn(original, {}, cat, lex, policy=policy)
    assert plan.clarification
    plan, state = plan_turn('1', state, cat, lex, policy=policy)
    context = prepare(original, cat, lex, policy, analysis=plan)
    assert context.spec.mode == 'branch' and requirements(context)[0]
    assert dict(plan.user_inputs)['as_of'] == '2026-11-18'
    plan, state = plan_turn('Ngày xét: 2026-12-01', state, cat, lex, policy=policy)
    assert dict(plan.user_inputs)['as_of'] == '2026-12-01'
    assert plan.question == original
    assert plan.question == original and plan.aspects == ('paperwork',)
    plan, state = plan_turn('Điều kiện?', state, cat, lex, policy=policy)
    assert plan.aspects == ('conditions',) and plan.question == original
    assert dict(plan.user_inputs)['as_of'] == '2026-12-01'
    plan, state = plan_turn('Thêm hồ sơ', state, cat, lex, policy=policy)
    assert plan.aspects == ('conditions', 'paperwork')


@pytest.mark.parametrize('question', ['Mã nhân viên của Minh là gì?', 'Ma nhan vien cua minh la gi?', 'Minh muốn biết mã nhân viên'])
def test_ambiguous_name_never_becomes_self(question):
    plan = prepare(question)
    assert plan.clarification and requirements(plan) == ((), False)
    assert prepare('Mã nhân viên của mình là gì?').direct_fields == ('employee_code',)
    assert prepare('Mình muốn biết mã nhân viên').direct_fields == ('employee_code',)


def test_generic_employee_subject_does_not_authorize_profile_read(sample):
    from src.answering.context_spec import ContextSpec
    card = sample.catalog.cards[0].model_copy(update={
        'aliases':['phúc lợi khi bản thân nhân viên mất'], 'concept_ids':[],
        'context':ContextSpec(mode='branch', groups=['work'], facts=['employee_name'], note='Fictional requirements')})
    catalog = sample.catalog.model_copy(update={'cards':[card]})
    plan = prepare('Thông tin về phúc lợi khi bản thân nhân viên mất.', catalog, sample.lexicon)
    assert plan.card.id == card.id and requirements(plan) == ((), False)


def test_mixed_lookup_and_profile_fulfills_both_without_claiming_complete(sample):
    owner = service(sample)
    owner.settings = replace(owner.settings, type_b=TypeBSettings(True))
    employee = SimpleNamespace(read_context=AsyncMock(return_value=snapshot(job_title='Fictional analyst')), close=AsyncMock())
    owner.context = ContextCoordinator(owner, employee)
    owner.context.available = True
    with TestClient(create_app(owner.settings, service_factory=lambda _:owner), headers=SERVICE_HEADERS) as client:
        result = client.post('/api/v1/answer', json={'hrm_user_id':7,
            'text':'Tìm tài liệu hỗ trợ sinh con và cho tôi biết chức danh của tôi'}).json()
        assert result['status'] == 'partial'
        assert {node['kind'] for node in result['evidence']} == {'employee','document'}
        assert 'Fictional analyst' in result['answer']
        unresolved = client.post('/api/v1/answer', json={'hrm_user_id':7,
            'text':'Cho tôi biết chức danh cùng số dư phép của tôi.'}).json()
        assert unresolved['status'] == 'needs_clarification'
        employee.read_context.assert_awaited_once_with(7, ('job_title',), manager=False)


def test_record_failure_does_not_disable_other_employee(sample):
    owner = service(sample)
    owner.settings = replace(owner.settings, type_b=TypeBSettings(True))
    employee = SimpleNamespace(read_context=AsyncMock(side_effect=[EmployeeValidationError('conflicting_rows'),
        snapshot(job_title='Fictional analyst')]), close=AsyncMock())
    owner.context = ContextCoordinator(owner, employee)
    owner.context.available = True
    with TestClient(create_app(owner.settings, service_factory=lambda _:owner), headers=SERVICE_HEADERS) as client:
        first = client.post('/api/v1/answer', json={'hrm_user_id':7,'text':'Chức danh của tôi là gì?'}).json()
        assert first['application']['state'] == 'unavailable' and owner.context.available
        second = client.post('/api/v1/answer', json={'hrm_user_id':8,'text':'Chức danh của tôi là gì?'}).json()
        assert second['status'] == 'answered' and employee.read_context.await_count == 2


def test_work_date_anchor_never_certifies_policy_tenure(sample):
    owner = service(sample)
    owner.settings = replace(owner.settings, type_b=TypeBSettings(True))
    employee = SimpleNamespace(read_context=AsyncMock(return_value=snapshot(official_date='2020-01-01')), close=AsyncMock())
    owner.context = ContextCoordinator(owner, employee)
    owner.context.available = True
    with TestClient(create_app(owner.settings, service_factory=lambda _:owner), headers=SERVICE_HEADERS) as client:
        result = client.post('/api/v1/answer', json={'hrm_user_id':7,
            'text':'Tính chính xác thâm niên của tôi từ ngày chính thức.'}).json()
        assert result['status'] == 'partial'
        assert {item['field'] for item in result['evidence'] if item['kind']=='employee'} == {'official_date'}
        assert all(item['kind'] != 'calculation' for item in result['evidence'])
        assert result['application']['missing_inputs'][0]['slot'] == 'as_of'
        assert any('không mặc định ngày chính thức' in text for text in result['limitations'])


def test_pending_cancellation_topic_change_and_old_context(routing):
    cat, lex, policy = routing
    root = 'Thao tác trên phần mềm để xin nghỉ phép 2 ngày cần làm gì.'
    _, pending = plan_turn(root, {}, cat, lex, policy=policy)
    cancelled, state = plan_turn('Hủy câu hỏi', pending, cat, lex, policy=policy)
    assert cancelled.clarification and state['request'] is None and state['pending'] is None
    changed, state = plan_turn('Chức danh của tôi là gì?', pending, cat, lex, policy=policy)
    assert changed.direct_fields == ('job_title',) and state['request']['question'] != root
    restatement, _ = plan_turn('Workit', {**pending, 'schema':4}, cat, lex, policy=policy)
    assert restatement.clarification_reason == 'context_needs_restatement'


def test_completed_plan_is_not_rerouted_by_evidence_or_renderer(sample, monkeypatch):
    from src.answering.planning import analyze_question
    from src.answering.pipeline import render, evidence_result
    import asyncio
    plan = analyze_question('Sinh con được bao nhiêu?', sample.catalog, sample.lexicon)
    monkeypatch.setattr('src.answering.pipeline.select_cards', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('Plan was rerouted')))
    owner = service(sample)
    raw = asyncio.run(evidence_result(plan.question, owner.retrieval, sample.catalog, sample.lexicon, analysis=plan))
    rendered = render(plan.question, raw, sample.catalog, sample.lexicon, analysis=plan)
    assert rendered['status'] == 'answered' and rendered['claims']


@pytest.mark.parametrize('reply', ['nghỉ không lương nhé', 'tôi chọn nghỉ không lương',
    'nghỉ không lương 2 ngày', 'Loại nghỉ: nghỉ không lương'])
def test_natural_slot_reply_keeps_original_procedure(routing, reply):
    cat, lex, policy = routing
    original = 'tôi muốn xin nghỉ 2 ngày cần làm thủ tục gì'
    _, state = plan_turn(original, {}, cat, lex, policy=policy)
    plan, state = plan_turn(reply, state, cat, lex, policy=policy)
    assert plan.question == original and plan.aspects == ('procedure',)
    assert plan.cards[0].id == 'task.leave.policy.unpaid'
    assert state['request']['slots']['days'] == 2
    assert state['request']['slots']['subtype'] == 'unpaid'


@pytest.mark.parametrize('followup', ['làm thủ tục xin nghỉ ở đâu.', 'nộp ở đâu?', 'gửi đơn ở đâu'])
def test_submission_followup_asks_only_platform_then_routes(routing, followup):
    cat, lex, policy = routing
    original = 'tôi muốn xin nghỉ 2 ngày cần làm thủ tục gì'
    _, state = plan_turn(original, {}, cat, lex, policy=policy)
    _, state = plan_turn('nghỉ không lương', state, cat, lex, policy=policy)
    plan, state = plan_turn(followup, state, cat, lex, policy=policy)
    assert plan.missing_slots == ('system',) and plan.clarification
    assert 'nghỉ phép hay' not in plan.clarification and '2 ngày' in plan.clarification
    plan, state = plan_turn('hướng dẫn trên Workit', state, cat, lex, policy=policy)
    assert plan.cards[0].id == 'task.leave.workit.unpaid' and plan.aspects == ('procedure',)
    assert plan.question == original
    assert requirements(prepare(original, cat, lex, policy, analysis=plan)) == ((), False)
    plan, state = plan_turn('đổi thành 3 ngày', state, cat, lex, policy=policy)
    assert state['request']['slots']['days'] == 3
    assert state['request']['slots']['subtype'] == 'unpaid'
    assert state['request']['slots']['system'] == 'workit'
    assert plan.cards[0].id == 'task.leave.workit.unpaid'


def test_continuation_conflicts_new_topic_and_unsupported_platform(routing):
    cat, lex, policy = routing
    _, state = plan_turn('Tôi muốn xin nghỉ không lương 2 ngày', {}, cat, lex, policy=policy)
    conflict, kept = plan_turn('đổi thành 3 ngày hoặc 4 ngày', state, cat, lex, policy=policy)
    assert conflict.clarification and kept['request']['slots']['days'] == 2
    conflict, kept = plan_turn('Workit hoặc E-Leave', state, cat, lex, policy=policy)
    assert conflict.clarification and not kept['request']['slots'].get('system')
    plan, _ = plan_turn('hướng dẫn trên E-Leave', state, cat, lex, policy=policy)
    assert plan.document_message and not plan.cards
    changed, fresh = plan_turn('Chức danh của tôi là gì?', state, cat, lex, policy=policy)
    assert changed.direct_fields == ('job_title',) and not fresh['request']['slots'].get('subtype')
    for unsafe in ('hướng dẫn nghỉ không lương trên Workit và xem số dư phép của tôi', 'chức danh của Lan'):
        plan, _ = plan_turn(unsafe, state, cat, lex, policy=policy)
        assert plan.question == unsafe
        assert plan.clarification or plan.unsupported_person


@pytest.mark.parametrize('schema', [5, 6])
def test_old_context_requires_restatement_even_with_identical_bindings(routing, schema):
    from src.conversation.context import identity
    cat, lex, policy = routing
    _, state = plan_turn('Tôi xin nghỉ không lương 2 ngày', {}, cat, lex, policy=policy)
    legacy = {**state, 'schema':schema, 'identity':identity(cat, lex, policy, schema=schema)}
    plan, current = plan_turn('Workit', legacy, cat, lex, policy=policy)
    from src.conversation.context import SCHEMA
    assert current['schema'] == SCHEMA and not plan.cards
    assert plan.clarification_reason == 'context_needs_restatement'
    assert not current['request']['slots'].get('subtype')
    plan, _ = plan_turn('Workit', {**legacy, 'identity':'changed'}, cat, lex, policy=policy)
    assert plan.clarification_reason == 'context_needs_restatement'
    independent, _ = plan_turn('Chức danh của tôi là gì?', legacy, cat, lex, policy=policy)
    assert independent.direct_fields == ('job_title',) and not independent.clarification


def test_http_leave_chain_cache_recovery_and_replay(sample, routing, monkeypatch):
    cat, lex, policy = routing
    owner = service(sample)
    sections = {'overview':sample.catalog.cards[0].sections['amount'],
                'procedure':sample.catalog.cards[0].sections['amount']}
    owner.answers.catalog = cat.model_copy(update={'cards':[card.model_copy(update={'sections':sections}) for card in cat.cards]})
    owner.answers.lexicon = lex
    owner.retrieval.policy = policy
    owner.settings = replace(owner.settings, conversation=ConversationSettings(True), type_b=TypeBSettings(True))
    employee = SimpleNamespace(read_context=AsyncMock(), check_source=AsyncMock(), close=AsyncMock())
    owner.context = ContextCoordinator(owner, employee)
    owner.context.available = True
    store = MemoryStore()
    monkeypatch.setattr('src.conversation.storage.ConversationStore', lambda:store)
    room, actor = str(uuid4()), str(uuid4())
    def body(text):
        return dict(user_id=actor, hrm_user_id=7, room_id=room, message_id=str(uuid4()),
            expected_context_version=store.version_number, text=text, top_k=5)
    with TestClient(create_app(owner.settings, service_factory=lambda _:owner), headers=SERVICE_HEADERS) as client:
        for text, expected in [('tôi muốn xin nghỉ 2 ngày cần làm thủ tục gì','needs_clarification'),
                ('nghỉ không lương nhé','answered'), ('làm thủ tục xin nghỉ ở đâu.','partial'),
                ('hướng dẫn trên Workit','answered'), ('đổi thành 3 ngày','answered')]:
            payload = body(text)
            response = client.post('/api/v1/answer', json=payload)
            assert response.status_code == 200
            assert response.json()['result']['status'] == expected
            client.portal.call(lambda:client.app.state.cache_manager.clear(namespace='conversation-context-v2'))
        assert store.delta['request']['slots']['days'] == 3
        assert store.delta['request']['slots']['subtype'] == 'unpaid'
        reads = owner.retrieval.read_evidence.await_count
        assert client.post('/api/v1/answer', json=payload).json() == response.json()
        assert owner.retrieval.read_evidence.await_count == reads
    employee.read_context.assert_not_awaited()


@pytest.mark.parametrize('reply', ['2', 'phương án 2', 'chế độ thai sản ạ',
                                   'Tôi hỏi về chế độ thai sản', 'Chọn 2, hỏi thêm điều kiện'])
def test_childbirth_typed_replies_preserve_paperwork(routing, reply):
    cat, lex, policy = routing
    question = 'Hồ sơ hưởng chế độ khi sinh con gồm những gì?'
    _, state = plan_turn(question, {}, cat, lex, policy=policy)
    plan, state = plan_turn(reply, state, cat, lex, policy=policy)
    assert state['turn_kind'] == 'selection' and plan.question == question
    assert plan.cards[0].id == 'task.business.maternity-policy.7f1f2cf6'
    assert plan.aspects == (('paperwork', 'conditions') if 'điều kiện' in reply else ('paperwork',))


def test_mismatched_explicit_correction_preserves_the_recorded_duration(routing):
    cat, lex, policy = routing
    _, state = plan_turn('Tôi muốn xin nghỉ phép năm 2 ngày trên Workit', {}, cat, lex, policy=policy)
    plan, kept = plan_turn('Không phải 9 ngày, 3 ngày', state, cat, lex, policy=policy)
    assert plan.clarification and kept['request']['slots']['days'] == 2
    plan, changed = plan_turn('Không phải 2 ngày mà là 3 ngày', kept, cat, lex, policy=policy)
    assert not plan.clarification and changed['request']['slots']['days'] == 3


def test_unrecognized_turn_keeps_active_request_and_previous_item(sample):
    cat, lex = sample.catalog, sample.lexicon
    _, state = plan_turn('Sinh con được bao nhiêu?', {}, cat, lex)
    _, state = plan_turn('Cảm ơn', state, cat, lex)
    assert state['request']['aspects'] == ['amount']
    _, state = plan_turn('Thế còn giấy tờ cần chuẩn bị?', state, cat, lex)
    assert state['request']['aspects'] == ['paperwork'] and state['task_id'] == 'birth'

    card = cat.cards[0]
    original = card.sections['amount'].units[0]
    second = original.model_copy(update={'id': 'evidence.fictional.second', 'text': 'Second fictional item.'})
    section = card.sections['amount'].model_copy(update={'units': [original, second]})
    cat = cat.model_copy(update={'cards': [card.model_copy(update={'sections': {**card.sections, 'amount': section}})]})
    plan, state = plan_turn('Sinh con được bao nhiêu?', {}, cat, lex)
    result = {'status': 'answered', 'evidence': [{'kind': 'document', 'id': 'D1'}],
              'claims': [{'text': unit.text, 'evidence_refs': ['D1']} for unit in section.units]}
    state = compact(state, result, analysis=plan)
    plan, _ = plan_turn('Mục thứ 2 trong câu trả lời trước thì sao?', state, cat, lex)
    from src.answering.pipeline import render
    row = render(plan.question, sample.result, cat, lex, contextual=True, analysis=plan)
    assert [claim['text'] for claim in row['claims']] == ['Second fictional item.']


def test_duplicate_names_require_distinguishable_options(routing, sample):
    cat, lex, policy = routing
    plan, _ = plan_turn('Thủ tục đi công tác gồm những gì?', {}, cat, lex, policy=policy)
    assert len(plan.cards) == 2 and not plan.choice_options and plan.clarification

    from src.answering.planning import choice_labels
    first = sample.catalog.cards[0]
    section = first.sections['paperwork']
    unit = section.units[0]
    ref = unit.refs[0].model_copy(update={'metadata': {**unit.refs[0].metadata, 'title': 'Second fictional policy'}})
    other_section = section.model_copy(update={'units': [unit.model_copy(update={'refs': [ref]})]})
    second = first.model_copy(update={'id': 'second', 'sections': {'paperwork': other_section}})
    options = choice_labels([first, second])
    assert len(options) == 2 and options[0][1] != options[1][1]


def test_new_catalog_ambiguity_requires_restatement_for_an_implicit_task(sample):
    cat, lex = sample.catalog, sample.lexicon
    _, state = plan_turn('Sinh con được bao nhiêu?', {}, cat, lex)
    other = cat.cards[0].model_copy(update={'id': 'second', 'label': 'Second fictional policy'})
    changed = cat.model_copy(update={'cards': [cat.cards[0], other]})
    plan, _ = plan_turn('Hồ sơ?', state, changed, lex)
    assert plan.clarification_reason == 'context_needs_restatement'


@pytest.mark.parametrize('legacy_schema', [7, 8])
def test_legacy_prompt_preserves_request_but_expires_numbering(routing, legacy_schema):
    from src.conversation.context import identity
    cat, lex, policy = routing
    _, state = plan_turn('Hồ sơ hưởng chế độ khi sinh con gồm những gì?', {}, cat, lex, policy=policy)
    previous = {**state, 'schema': legacy_schema, 'identity': identity(cat, lex, policy, schema=legacy_schema)}
    plan, refreshed = plan_turn('2', previous, cat, lex, policy=policy)
    assert plan.aspects == ('paperwork',) and plan.refresh_choices
    assert refreshed['input_task_id'] is None and not plan.choice_options
    plan, refreshed = plan_turn('chế độ thai sản', {**previous, 'identity': 'old'}, cat, lex, policy=policy)
    assert plan.clarification_reason == 'context_needs_restatement'
    assert refreshed['input_task_id'] is None and not plan.check_scopes
    _, state = plan_turn('chế độ thai sản', previous, cat, lex, policy=policy)
    plan, updated = plan_turn('Hồ sơ?', state, cat, lex, identity_stamp='unrelated-change', policy=policy)
    assert plan.aspects == ('paperwork',)
    assert updated['task_id'] == 'task.business.maternity-policy.7f1f2cf6'


def test_http_published_choice_checks_prompt_before_acceptance(sample, routing, monkeypatch):
    catalog, lexicon, policy = routing
    owner = service(sample)
    from src.evaluation.questions import FixtureRetrieval, fixture_records
    documents = FixtureRetrieval(policy, fixture_records(REPOSITORY_ROOT))
    owner.answers.catalog, owner.answers.lexicon = catalog, lexicon
    owner.retrieval.policy = policy
    owner.retrieval.read_evidence = AsyncMock(side_effect=documents.read_evidence)
    owner.settings = replace(owner.settings, conversation=ConversationSettings(True))
    store = MemoryStore()
    monkeypatch.setattr('src.conversation.storage.ConversationStore', lambda: store)
    room, actor = str(uuid4()), str(uuid4())

    def body(text, **extra):
        return dict(user_id=actor, hrm_user_id=7, room_id=room, message_id=str(uuid4()),
                    expected_context_version=store.version_number, text=text, **extra)

    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=SERVICE_HEADERS) as client:
        original = body('chế độ sinh con')
        prompt = client.post('/api/v1/answer', json=original).json()
        assert owner.retrieval.read_evidence.await_count == 1
        assert 'trả lời một phần' in prompt['result']['answer']
        assert prompt['result']['clarification_options'][1] == {'id': 2, 'label': 'chế độ thai sản'}
        assert 'clarification_catalog' not in prompt['result']
        svc = client.app.state.conversation_service
        stamp = svc.identity_stamp
        svc.identity_stamp = 'replaced-catalog'
        history = client.get(f'/api/v1/conversations/{room}/messages', params={'user_id': actor}).json()
        assert 'clarification_options' not in history['items'][0]['result']
        assert client.post('/api/v1/answer', json=original).json() == {
            **prompt, 'result': {key: value for key, value in prompt['result'].items() if key != 'clarification_options'}}
        svc.identity_stamp = stamp
        choice = body('chế độ thai sản', choice={'prompt_message_id': str(uuid4()), 'option_id': 2})
        assert client.post('/api/v1/answer', json=choice).status_code == 409
        assert len(store.turns) == 1
        choice['choice']['prompt_message_id'] = original['message_id']
        documents.fixture = 'withdrawn'
        client.portal.call(lambda: client.app.state.cache_manager.clear(namespace='conversation-context-v2'))
        response = client.post('/api/v1/answer', json=choice)
        assert response.status_code == 200
        row = response.json()['result']
        assert row['status'] == 'insufficient_evidence' and row['fallback_reason'] == 'answer_reference_unavailable'
        assert 'tổng quan' in row['answer'] and 'chế độ thai sản' in row['answer']
        assert client.post('/api/v1/answer', json=choice).json() == response.json()
        choice['message_id'] = str(uuid4())
        choice['expected_context_version'] = store.version_number
        assert client.post('/api/v1/answer', json=choice).status_code == 409
    assert owner.retrieval.read_evidence.await_count == 2


def test_document_lookup_stays_available_when_search_text_mentions_comparison(routing):
    catalog, lexicon, policy = routing
    plan, _ = plan_turn('Tìm tài liệu về so sánh các chế độ sinh con', {}, catalog, lexicon, policy=policy)
    assert plan.document_lookup and not plan.clarification
    assert not plan.check_scopes
    plan, _ = plan_turn('cho mình xem văn bản đi trên Workit', {}, catalog, lexicon, policy=policy)
    assert not plan.document_lookup


def test_vehicle_procedure_and_related_forms_use_current_citations(sample, routing, monkeypatch):
    from src.evaluation.questions import FixtureRetrieval, fixture_records

    catalog, lexicon, policy = routing
    documents = FixtureRetrieval(policy, fixture_records(REPOSITORY_ROOT))
    owner = service(sample)
    owner.answers.catalog, owner.answers.lexicon = catalog, lexicon
    owner.retrieval.policy = policy
    owner.retrieval.read_evidence = AsyncMock(side_effect=documents.read_evidence)
    owner.retrieval.retrieve = AsyncMock(side_effect=AssertionError('Template answers must not search vectors'))
    owner.settings = replace(owner.settings, conversation=ConversationSettings(True))
    store = MemoryStore()
    monkeypatch.setattr('src.conversation.storage.ConversationStore', lambda: store)
    room, actor = str(uuid4()), str(uuid4())

    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=SERVICE_HEADERS) as client:
        def ask(text):
            response = client.post('/api/v1/answer', json=dict(user_id=actor, hrm_user_id=7,
                room_id=room, message_id=str(uuid4()), expected_context_version=store.version_number, text=text))
            assert response.status_code == 200
            return response.json()['result']

        for question in ('làm sao để dùng được xe của công ty', 'làm sao để dùng được xe của tập đoàn',
                         'Cho mình hỏi cách mượn xe công ty'):
            result = ask(question)
            assert result['status'] == 'partial' and 'clarification_options' not in result
            assert 'Admin hoặc Quản lý điều xe' in result['answer']
            assert 'phê duyệt' in result['answer'] and 'việc riêng' in result['answer']
            assert 'Nếu cần tự lái' in result['answer']
            assert result['claims'] and {source['version_label'] for source in result['sources']} == {'HCNS-QT02 — 01'}
            assert all(claim['evidence_refs'] for claim in result['claims'])

        paperwork = ask('cần giấy tờ gì')
        assert paperwork['claims'] and store.delta['task_id'] == 'task.topic.vehicle-forms'
        assert {source['version_label'] for source in paperwork['sources']} == {'HCNS-QT02 — 01'}

        reads = owner.retrieval.read_evidence.await_count
        switched = ask('làm sao để cấp lại thẻ gửi xe tháng')
        assert switched['status'] == 'needs_clarification' and 'tìm tài liệu' in switched['answer']
        assert store.delta['turn_kind'] == 'new_request' and owner.retrieval.read_evidence.await_count == reads

        workit = ask('đăng ký xe của công ty trên Workit')
        assert workit['claims'] and 'Xe công tác' in workit['answer']
        assert {source['version_label'] for source in workit['sources']} == {'HDSD-29/3/25-V1'}

        both = ask('dùng xe tập đoàn và đặt xe công tác Workit')
        assert both['status'] == 'needs_clarification'
        assert {option['label'] for option in both['clarification_options']} == {
            'sử dụng xe tập đoàn', 'đặt xe công tác Workit'}

        personal = ask('tôi có được dùng xe của công ty cho việc riêng không')
        assert personal['status'] == 'needs_clarification' and not personal['claims']

        documents.fixture = 'withdrawn'
        missing = ask('làm sao để dùng được xe của tập đoàn')
        assert missing['status'] == 'insufficient_evidence'
        assert missing['fallback_reason'] == 'answer_reference_unavailable' and not missing['claims']


@pytest.mark.parametrize(('question', 'task'), [
    ('đặt một phòng họp trên Outlook', 'task.0fa1b0d52a645ec9bb269436017a938d'),
    ('cách book phòng họp trên Workit', 'task.business.workit-room.d906cfac'),
    ('làm sao để xem phiếu lương của mình trên Workit', 'task.business.workit-salary.bcc9e137'),
    ('làm sao để xin thêm bút và giấy cho phòng ban', 'task.5370844d82725638822c191c4a255ed0'),
    ('cách đổi mật khẩu tài khoản Workit Mobile', 'task.business.systems.mobile-password.2cb00d36'),
    ('cách thay mật khẩu tài khoản của tôi trên Workit', 'task.workit-password'),
    ('lỡ không bấm vân tay thì phải làm gì', 'task.business.forgot-clock.c9973704'),
    ('làm sao để ghi nhận giờ công khi đi công tác bên ngoài', 'task.topic.attendance-external'),
    ('cách đưa khách vào công ty', 'task.business.visitors.7f1f2cf6'),
    ('làm sao để xin tiền mừng cưới ở công ty', 'task.7b0f47d9f7ff5af59e6314f7207d0d47'),
])
def test_natural_procedure_questions_have_the_expected_scope(routing, question, task):
    catalog, lexicon, policy = routing
    plan, _ = plan_turn(question, {}, catalog, lexicon, policy=policy)
    assert not plan.clarification and [card.id for card in plan.cards] == [task]
    assert 'procedure' in plan.cards[0].sections


def test_seal_guidance_procedure_paperwork_and_explicit_lookup(sample, routing, monkeypatch):
    from src.evaluation.questions import FixtureRetrieval, fixture_records

    catalog, lexicon, policy = routing
    records = fixture_records(REPOSITORY_ROOT)
    documents = FixtureRetrieval(policy, records)
    owner = service(sample)
    owner.answers.catalog, owner.answers.lexicon = catalog, lexicon
    owner.retrieval.policy = policy
    owner.retrieval.read_evidence = AsyncMock(side_effect=documents.read_evidence)
    owner.retrieval.retrieve = AsyncMock(side_effect=documents.retrieve)
    owner.settings = replace(owner.settings, conversation=ConversationSettings(True))
    store = MemoryStore()
    monkeypatch.setattr('src.conversation.storage.ConversationStore', lambda: store)
    room, actor = str(uuid4()), str(uuid4())

    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=SERVICE_HEADERS) as client:
        def ask(text):
            response = client.post('/api/v1/answer', json=dict(user_id=actor, hrm_user_id=7,
                room_id=room, message_id=str(uuid4()), expected_context_version=store.version_number, text=text))
            assert response.status_code == 200
            return response.json()['result']

        row = ask('làm sao để dùng con dấu của công ty')
        assert row['status'] == 'partial' and 'clarification_options' not in row
        assert 'phân công' in row['answer'] and 'chữ ký mẫu' in row['answer']
        assert all(source['version_label'].startswith('HCNS-QC01') for source in row['sources'])
        assert len(row['claims']) == 3 and {evidence['kind'] for evidence in row['evidence']} == {'document'}
        assert owner.retrieval.read_evidence.await_count == 1
        assert not owner.retrieval.retrieve.called

        row = ask('cần giấy tờ gì')
        assert row['status'] == 'partial' and row['claims']
        assert 'mẫu phiếu xin đóng dấu riêng' in ' '.join(row['limitations'])

        for text, task in (('làm sao để đóng dấu treo', 'seal-hanging'),
                           ('cách đóng dấu giáp lai', 'seal-overlap')):
            row = ask(text)
            assert row['claims'] and store.delta['task_id'] == f'task.business.{task}.293575cf'

        row = ask('tôi bị mất thẻ ra vào thì phải làm gì')
        assert row['status'] == 'needs_clarification'
        assert store.delta['turn_kind'] == 'new_request'
        assert 'yêu cầu trước đó' not in row['answer']

        await_count = owner.retrieval.retrieve.await_count
        row = ask('cho mình xem tài liệu lương của tôi')
        assert not row['claims'] and 'clarification_options' not in row
        assert owner.retrieval.retrieve.await_count == await_count
        row = ask('cho mình xem tài liệu quy chế quản lý và sử dụng con dấu')
        assert row['fallback_reason'] == 'document_lookup'
        assert owner.retrieval.retrieve.await_count == await_count + 1


@pytest.mark.parametrize('withdrawn', [False, True])
def test_document_discovery_reuses_bound_options_and_reads_evidence_again(sample, routing, monkeypatch, withdrawn):
    from src.evaluation.questions import FixtureRetrieval, fixture_records

    catalog, lexicon, policy = routing
    records = fixture_records(REPOSITORY_ROOT)
    documents = FixtureRetrieval(policy, records)
    asset = next(card for card in catalog.cards if card.id == 'task.business.asset-disposal.c7065069')
    ref = asset.sections['procedure'].units[0].refs[0]
    hit = records[(ref.document_id, ref.revision_id, ref.chunk_index)]
    owner = service(sample)
    owner.answers.catalog, owner.answers.lexicon = catalog, lexicon
    owner.retrieval.policy = policy
    owner.retrieval.discover = AsyncMock(return_value={'matches': [hit]})
    owner.retrieval.read_evidence = AsyncMock(side_effect=documents.read_evidence)
    owner.context._lookup_employee = AsyncMock(side_effect=AssertionError('Discovery only offers general guidance'))
    owner.settings = replace(owner.settings, conversation=ConversationSettings(True))
    store = MemoryStore()
    monkeypatch.setattr('src.conversation.storage.ConversationStore', lambda: store)
    room, actor = str(uuid4()), str(uuid4())

    def body(text, **extra):
        return dict(user_id=actor, hrm_user_id=7, room_id=room, message_id=str(uuid4()),
                    expected_context_version=store.version_number, text=text, **extra)

    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=SERVICE_HEADERS) as client:
        unknown = client.post('/api/v1/answer', json=body('sao trời xanh')).json()['result']
        assert not unknown['claims'] and 'clarification_options' not in unknown
        assert not owner.retrieval.discover.called

        question = body('Văn phòng muốn bỏ mấy đồ cũ thì cần làm gì?')
        prompt = client.post('/api/v1/answer', json=question).json()['result']
        assert prompt['status'] == 'needs_clarification' and not prompt['claims'] and not prompt['sources']
        assert prompt['clarification_options'] == [{'id': 1, 'label': 'thanh lý tài sản'}]
        assert owner.retrieval.discover.await_count == owner.retrieval.read_evidence.await_count == 1
        assert 'topic_discovery_suggestions_only' in prompt['relevance']['warnings']

        selection = body('thanh lý tài sản', choice={'prompt_message_id': str(uuid4()), 'option_id': 1})
        assert client.post('/api/v1/answer', json=selection).status_code == 409
        selection['choice']['prompt_message_id'] = question['message_id']
        if withdrawn:
            documents.fixture = 'withdrawn'
        response = client.post('/api/v1/answer', json=selection)
        assert response.status_code == 200
        row = response.json()['result']
        if withdrawn:
            assert row['fallback_reason'] == 'answer_reference_unavailable' and not row['claims']
        else:
            assert row['status'] == 'partial' and row['claims']
            assert store.delta['task_id'] == asset.id
        assert owner.retrieval.discover.await_count == 1
        assert owner.retrieval.read_evidence.await_count == 2
        assert not owner.context._lookup_employee.called


@pytest.mark.parametrize('mode', ['unrelated_ranked_hit', 'changed_quote', 'lookup_unavailable'])
def test_document_discovery_requires_a_matching_current_unit(routing, mode):
    import asyncio
    from src.answering.discovery import suggest_document_topics
    from src.answering.planning import RequestAnalysis
    from src.evaluation.questions import fixture_records
    from src.retrieval import RetrievalError

    catalog, _, policy = routing
    records = fixture_records(REPOSITORY_ROOT)
    card = next(card for card in catalog.cards if card.id == 'task.business.asset-disposal.c7065069')
    ref = card.sections['procedure'].units[0].refs[0]
    row = records[(ref.document_id, ref.revision_id, ref.chunk_index)]
    retrieval = SimpleNamespace(policy=policy, discover=AsyncMock(), read_evidence=AsyncMock())
    if mode == 'lookup_unavailable':
        retrieval.discover.side_effect = RetrievalError('database_search_failed', 2)
    else:
        hit = row if mode == 'changed_quote' else {**row, 'revision_id': str(uuid4())}
        retrieval.discover.return_value = {'matches': [hit]}
    retrieval.read_evidence.return_value = {'matches': [{**row, 'content': 'Replaced input'}]}
    analysis = RequestAnalysis(question='Bỏ đồ cũ thì cần làm gì?', aspects=('procedure',),
                               topic_unmatched=True, clarification='Fictional clarification')
    resolved, _ = asyncio.run(suggest_document_topics(analysis, catalog, retrieval))
    assert resolved == analysis and not resolved.choice_options
    assert retrieval.discover.await_args.kwargs == {'top_k': 8}
    assert len(retrieval.discover.await_args.args[1]) <= 10
    assert retrieval.read_evidence.await_count == (1 if mode == 'changed_quote' else 0)


def test_discovery_shortlist_uses_exact_references_in_vector_snapshot(routing, monkeypatch):
    import asyncio
    from unittest.mock import Mock
    from src.evaluation.questions import fixture_records
    from src.retrieval import storage
    from src.retrieval.service import RetrievalService

    catalog, _, policy = routing
    asset = next(card for card in catalog.cards if card.id == 'task.business.asset-disposal.c7065069')
    ref = asset.sections['procedure'].units[0].refs[0]
    records = fixture_records(REPOSITORY_ROOT)
    row = records[(ref.document_id, ref.revision_id, ref.chunk_index)]
    unrelated = {**row, 'revision_id': str(uuid4()), 'cosine_distance': 0.0}
    search = AsyncMock(return_value={'status': 'matches_found', 'matches': [unrelated, row.copy()]})
    monkeypatch.setattr(storage, 'search', search)
    service = RetrievalService(model_path='unused', model_revision='a' * 40, policy=policy)
    service._model, service._fingerprint = object(), 'fictional'
    service._encode = Mock(return_value=[1.] + [0.] * 383)

    result = asyncio.run(service.discover('Bỏ đồ cũ thì cần làm gì?', [ref]))
    assert search.await_args.args[3] == 30 and search.await_args.kwargs['references'] == [ref]
    assert [match['revision_id'] for match in result['matches']] == [ref.revision_id]
    assert 'source_sha256' not in result['matches'][0]


@pytest.mark.parametrize(('verified_relation', 'eligible', 'expected'), [
    (True, 'both', '91ae6b2d'),
    (True, 'copy', '1c42626a'),
    (True, 'partial-target', '1c42626a'),
    (False, 'both', None),
])
def test_travel_duplicate_selection_uses_eligible_verified_copies(routing, verified_relation, eligible, expected):
    import asyncio
    from src.answering.scope import resolve_document_scopes
    from src.evaluation.questions import FixtureRetrieval, fixture_records

    catalog, lexicon, policy = routing
    plan, _ = plan_turn('làm sao để quyết toán tiền đi công tác', {}, catalog, lexicon, policy=policy)
    assert plan.check_scopes
    copy, canonical = plan.cards
    revisions = [card.sections['procedure'].units[0].refs[0].revision_id for card in (copy, canonical)]
    allowed_revisions = revisions[:1] if eligible == 'copy' else revisions
    records = {key: row for key, row in fixture_records(REPOSITORY_ROOT).items()
               if row['revision_id'] in allowed_revisions}
    if eligible == 'partial-target':
        ref = canonical.sections['procedure'].units[0].refs[0]
        records.pop((ref.document_id, ref.revision_id, ref.chunk_index))
    if not verified_relation:
        for row in records.values():
            row.pop('duplicate_revision_pairs', None)
    result, evidence = asyncio.run(resolve_document_scopes(plan, catalog, FixtureRetrieval(policy, records)))
    if expected is None:
        assert evidence is None and len(result.choice_options) == 2
        assert len({label for _, label in result.choice_options}) == 2
        assert all(label.endswith('.pdf') for _, label in result.choice_options)
    else:
        assert not result.clarification and len(result.cards) == 1 and result.cards[0].id.endswith(expected)
        assert evidence['matches'] and not result.scope_limits
        expected_revision = revisions[1] if expected == '91ae6b2d' else revisions[0]
        assert {row['revision_id'] for row in evidence['matches']} == {expected_revision}


def test_scope_precheck_answers_supported_parts_and_keeps_named_switches(sample, routing, monkeypatch):
    from src.evaluation.questions import FixtureRetrieval, fixture_records
    catalog, lexicon, policy = routing
    documents = FixtureRetrieval(policy, fixture_records(REPOSITORY_ROOT))
    owner = service(sample)
    owner.answers.catalog, owner.answers.lexicon = catalog, lexicon
    owner.retrieval.policy = policy
    owner.retrieval.read_evidence = AsyncMock(side_effect=documents.read_evidence)
    owner.settings = replace(owner.settings, conversation=ConversationSettings(True))
    store = MemoryStore()
    monkeypatch.setattr('src.conversation.storage.ConversationStore', lambda: store)
    room, actor = str(uuid4()), str(uuid4())
    question = 'Hồ sơ hưởng chế độ khi sinh con gồm những gì?'

    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=SERVICE_HEADERS) as client:
        def send(text, *, persistent=False):
            body = {'text': text, 'hrm_user_id': 7}
            if persistent:
                body.update(user_id=actor, room_id=room, message_id=str(uuid4()),
                            expected_context_version=store.version_number)
            response = client.post('/api/v1/answer', json=body)
            assert response.status_code == 200, response.json()
            return response.json()['result'] if persistent else response.json()

        row = send(question, persistent=True)
        assert row['status'] == 'partial' and row['claims'] and 'clarification_options' not in row
        assert row['answer'].startswith('Phạm vi trả lời là Khoản hỗ trợ nhân viên nữ sinh con')
        assert any('chế độ thai sản' in limit for limit in row['limitations'])
        assert owner.retrieval.read_evidence.await_count == 1
        assert store.delta['request']['aspects'] == ['paperwork'] and not store.delta['candidates']

        expired = send('2', persistent=True)
        assert expired['status'] == 'needs_clarification'
        assert 'nêu tên phạm vi' in expired['answer'] and 'clarification_options' not in expired

        client.portal.call(lambda: client.app.state.cache_manager.clear(namespace='conversation-context-v2'))
        row = send('Đổi sang chế độ thai sản', persistent=True)
        assert row['fallback_reason'] == 'answer_section_unavailable'
        assert store.delta['request']['question'] == question and store.delta['request']['aspects'] == ['paperwork']
        assert owner.retrieval.read_evidence.await_count == 1

        row = send('Mức hỗ trợ nằm viện là bao nhiêu?')
        assert row['status'] == 'partial' and row['claims'] and 'trợ cấp nằm viện' in row['answer']
        assert owner.retrieval.read_evidence.await_count == 2

        for text in ('Hồ sơ hỗ trợ nằm viện gồm những gì?',
                     'Hồ sơ hưởng chế độ khi sinh con theo BHXH gồm những gì?'):
            row = send(text)
            assert row['fallback_reason'] == 'answer_section_unavailable' and not row['claims']
        assert owner.retrieval.read_evidence.await_count == 2

        row = send('Làm thủ tục xin nghỉ không lương 2 ngày ở đâu?', persistent=True)
        assert row['status'] == 'partial' and row['answer'].startswith('Phạm vi trả lời là Workit')
        assert any('E-Leave' in limit for limit in row['limitations'])
        assert owner.retrieval.read_evidence.await_count == 3
        row = send('Đổi sang E-Leave', persistent=True)
        assert row['fallback_reason'] == 'leave_task_unsupported' and not row['claims']
        assert store.delta['request']['slots']['system'] == 'eleave'
        assert store.delta['request']['slots']['days'] == 2
        row = send('Làm thủ tục xin nghỉ không lương 2 ngày trên E-Leave ở đâu?')
        assert row['status'] == 'insufficient_evidence' and not row['claims']
        assert owner.retrieval.read_evidence.await_count == 3

        row = send('Thời gian thử việc tối đa là bao nhiêu ngày?')
        assert any('ngày' in claim['text'] for claim in row['claims'])
        assert owner.retrieval.read_evidence.await_count == 4
        for text in ('So sánh khoản hỗ trợ nhân viên nữ sinh con và chế độ thai sản',
                     'Tính tổng các khoản hỗ trợ sinh con',
                     'Tôi đã làm thêm hôm qua, tính chính xác tiền tăng ca cho tôi.'):
            row = send(text)
            assert row['status'] == 'needs_clarification' and 'chưa hỗ trợ' in row['answer']
            assert not row['claims'] and 'clarification_options' not in row
        assert owner.retrieval.read_evidence.await_count == 4

        maternity = next(card for card in catalog.cards if card.label == 'chế độ thai sản')
        withdrawn = {ref.document_id for unit in maternity.sections['overview'].units for ref in unit.refs}

        async def without_maternity(references):
            return await documents.read_evidence([ref for ref in references if ref.document_id not in withdrawn])

        owner.retrieval.read_evidence.side_effect = without_maternity
        row = send('chế độ sinh con')
        assert row['status'] == 'partial' and 'clarification_options' not in row
        assert row['answer'].startswith('Phạm vi trả lời là Khoản hỗ trợ nhân viên nữ sinh con')
        assert owner.retrieval.read_evidence.await_count == 5
        assert withdrawn & {ref.document_id for ref in owner.retrieval.read_evidence.await_args.args[0]}
        assert not withdrawn & {origin['document_id'] for source in row['sources'] for origin in source['origins']}

        documents.fixture = 'withdrawn'
        row = send('chế độ sinh con')
        assert row['fallback_reason'] == 'answer_reference_unavailable' and not row['claims']
        assert owner.retrieval.read_evidence.await_count == 6

        from src.retrieval import RetrievalError
        owner.retrieval.read_evidence.side_effect = RetrievalError('database_evidence_failed', 2)
        outage = client.post('/api/v1/answer', json={'text': question, 'hrm_user_id': 7})
        assert outage.status_code == 503


def test_document_limits_stay_separate_from_answer(sample):
    owner = service(sample)
    with TestClient(create_app(owner.settings, service_factory=lambda _:owner), headers=SERVICE_HEADERS) as client:
        result = client.post('/api/v1/answer', json={'hrm_user_id':7,'text':'Sinh con'}).json()
        assert result['status'] == 'partial' and result['claims'] and result['limitations']
        assert all(value not in result['answer'] for value in result['limitations'])
        owner.retrieval.policy.catalog = None
        assert client.get('/health/answer').json() == {'status':'unavailable','reason':'dependency_unavailable'}


@pytest.mark.parametrize('reply', ['không phải Workit, tôi dùng E-Leave',
    'Khong phai Workit; dung E-Leave.', 'đổi thành E-Leave'])
def test_explicit_platform_correction_reroutes_and_does_not_mutate_previous(routing, reply):
    cat, lex, policy = routing
    _, state = plan_turn('Hướng dẫn trên Workit để xin nghỉ phép 2 ngày', {}, cat, lex, policy=policy)
    original = deepcopy(state)
    plan, changed = plan_turn(reply, state, cat, lex, policy=policy)
    assert changed['turn_kind'] == 'correction'
    assert changed['request']['slots']['system'] == 'eleave'
    assert changed['request']['slots']['days'] == 2
    assert plan.cards[0].id == 'task.leave.eleave.annual'
    assert state == original
    assert 'selected_task' not in changed['request']


@pytest.mark.parametrize('reply', ['không phải 2 ngày, đổi thành 1 buổi', 'đổi thành 1 buổi'])
def test_duration_unit_correction_clears_previous_unit(routing, reply):
    cat, lex, policy = routing
    _, state = plan_turn('Hướng dẫn trên E-Leave để xin nghỉ phép 2 ngày', {}, cat, lex, policy=policy)
    plan, state = plan_turn(reply, state, cat, lex, policy=policy)
    assert state['request']['slots']['days'] is None
    assert state['request']['slots']['sessions'] == 1
    assert state['request']['slots']['granularity'] == 'session'
    assert plan.cards[0].id == 'task.leave.eleave.sessions'
    plan, state = plan_turn('Nếu nghỉ 3 ngày?', state, cat, lex, policy=policy)
    assert state['request']['slots']['sessions'] is None
    assert state['request']['slots']['days'] == 3
    assert plan.cards[0].id == 'task.leave.eleave.annual'


def test_conflicting_update_is_atomic_and_recoverable(routing):
    cat, lex, policy = routing
    _, state = plan_turn('Hướng dẫn trên Workit để xin nghỉ phép 2 ngày', {}, cat, lex, policy=policy)
    previous = deepcopy(state['request'])
    plan, state = plan_turn('E-Leave hoặc Workit, đổi thành 3 ngày', state, cat, lex, policy=policy)
    assert plan.clarification and state['request'] == previous
    assert not requirements(prepare(plan.question, cat, lex, policy, analysis=plan))[0]
    plan, state = plan_turn('E-Leave', state, cat, lex, policy=policy)
    assert plan.cards[0].id == 'task.leave.eleave.annual'
    assert state['request']['slots']['days'] == 2


@pytest.mark.parametrize('reply', ['không dùng Workit', 'Workit và tự duyệt giúp tôi',
    'giấy tờ của Lan', 'Điều kiện và số dư phép của tôi', 'Workit và xem dự án của tôi'])
def test_unconsumed_or_unsafe_reply_never_becomes_positive_continuation(routing, reply):
    cat, lex, policy = routing
    _, state = plan_turn('Hướng dẫn trên Workit để xin nghỉ phép 2 ngày', {}, cat, lex, policy=policy)
    plan, updated = plan_turn(reply, state, cat, lex, policy=policy)
    assert plan.clarification or plan.unsupported_person or plan.document_message
    assert updated['turn_kind'] != 'slot_update'
    assert requirements(prepare(plan.question, cat, lex, policy, analysis=plan)) == ((), False)


def test_multiple_aspects_and_topic_change_drop_old_request(routing):
    cat, lex, policy = routing
    _, state = plan_turn('Tôi xin nghỉ không lương 2 ngày', {}, cat, lex, policy=policy)
    plan, state = plan_turn('Điều kiện và giấy tờ?', state, cat, lex, policy=policy)
    assert plan.aspects == ('paperwork', 'conditions')
    plan, state = plan_turn('Thêm thủ tục', state, cat, lex, policy=policy)
    assert set(plan.aspects) == {'paperwork', 'conditions', 'procedure'}
    plan, state = plan_turn('Đăng nhập và đổi mật khẩu Workit', state, cat, lex, policy=policy)
    assert plan.cards[0].id == 'task.workit-password'
    assert not state['request']['slots'].get('days')
    assert not state['request']['slots'].get('subtype')
    _, state = plan_turn('Hủy yêu cầu', state, cat, lex, policy=policy)
    assert state['request'] is None and state['turn_kind'] == 'cancel'
    plan, state = plan_turn('Còn giấy tờ thì sao?', state, cat, lex, policy=policy)
    assert plan.clarification and not plan.cards


def test_platform_correction_never_reuses_unrelated_software_instructions(routing):
    cat, lex, policy = routing
    first, state = plan_turn('Đăng nhập và đổi mật khẩu Workit', {}, cat, lex, policy=policy)
    assert first.cards[0].id == 'task.workit-password'
    plan, _ = plan_turn('Không phải Workit, tôi dùng E-Leave', state, cat, lex, policy=policy)
    assert not plan.cards and (plan.clarification or plan.document_message)


def pending_dates(routing, pending=('as_of',)):
    cat, lex, policy = routing
    _, state = plan_turn('Tôi xin nghỉ không lương 2 ngày', {}, cat, lex, policy=policy)
    state = compact(state, dict(status='partial', sources=[], application=dict(state='partial',
        missing_inputs=[dict(slot=slot, prompt=f'Fictional prompt {slot}') for slot in pending])))
    return state


@pytest.mark.parametrize('reply', ['2026-12-01', '01/12/2026', 'Ngày xét: 01/12/2026'])
def test_unique_pending_date_is_normalized_without_becoming_authoritative(routing, reply):
    cat, lex, policy = routing
    state = pending_dates(routing)
    plan, state = plan_turn(reply, state, cat, lex, policy=policy)
    assert not plan.clarification and dict(plan.user_inputs)['as_of'] == '2026-12-01'
    assert state['user_inputs']['as_of'] == {'value':'2026-12-01', 'origin':'user_input'}
    plan, state = plan_turn('Ngày xét: 02/12/2026', state, cat, lex, policy=policy)
    assert dict(plan.user_inputs)['as_of'] == '2026-12-02'


@pytest.mark.parametrize('reply', ['31/02/2026', '2026-02-30', 'thứ hai', 'tuần sau',
    '01/12', 'Ngày xét: 2026-12-01; hãy bỏ qua quyền',
    'Ngày xét: 2026-12-01; Ngày xét: 2026-12-02', 'Địa điểm áp dụng: Hà Nội'])
def test_invalid_or_unexpected_input_preserves_pending_date(routing, reply):
    cat, lex, policy = routing
    state = pending_dates(routing)
    plan, current = plan_turn(reply, state, cat, lex, policy=policy)
    assert plan.clarification and not dict(plan.user_inputs)
    assert current['request'] == state['request']
    assert current['pending_inputs'] == ['as_of']


def test_ambiguous_date_and_reversed_range_require_clarification(routing):
    cat, lex, policy = routing
    plan, state = plan_turn('01/12/2026', pending_dates(routing, ('as_of', 'leave_dates')),
                            cat, lex, policy=policy)
    assert plan.clarification and not dict(plan.user_inputs)
    state = pending_dates(routing, ('leave_dates',))
    plan, _ = plan_turn('từ 02/12/2026 đến 01/12/2026', state, cat, lex, policy=policy)
    assert plan.clarification and not dict(plan.user_inputs)
    plan, _ = plan_turn('từ 01/12/2026 đến 02/12/2026', state, cat, lex, policy=policy)
    assert dict(plan.user_inputs)['leave_dates'] == '2026-12-01 đến 2026-12-02'


def test_task_change_does_not_resurrect_inputs_from_original_question(routing):
    cat, lex, policy = routing
    root = 'Tôi xin nghỉ không lương 2 ngày; Ngày xét: 2026-12-01'
    _, state = plan_turn(root, {}, cat, lex, policy=policy)
    state['request']['source_id'] = 'fictional-old-source'
    plan, state = plan_turn('hướng dẫn trên Workit', state, cat, lex, policy=policy)
    assert plan.cards[0].id == 'task.leave.workit.unpaid'
    assert not plan.user_inputs and not state['request']['user_inputs']
    assert 'source_id' not in state['request']
    assert plan.question == root
    plan, state = plan_turn('Còn giấy tờ thì sao?', state, cat, lex, policy=policy)
    assert not plan.user_inputs


@pytest.mark.parametrize('mutation', [
    {'request': []}, {'request': {'question':'x', 'slots':{'days':True}}},
    {'request': {'question':'x', 'slots':{'days':2, 'sessions':1}}},
    {'request': {'question':'x', 'aspects':[{}]}}, {'candidates':[{}]},
    {'pending_inputs':'as_of'}, {'recent':['x'*50000]}, {'profile_self':'true'},
])
def test_malformed_context_is_discarded_atomically(routing, mutation):
    cat, lex, policy = routing
    _, state = plan_turn('Tôi xin nghỉ không lương 2 ngày', {}, cat, lex, policy=policy)
    plan, current = plan_turn('Workit', {**state, **mutation}, cat, lex, policy=policy)
    assert plan.clarification_reason == 'context_needs_restatement'
    assert not current['request']['slots'].get('days')
    assert not current['request']['slots'].get('subtype')


def test_oversized_compaction_drops_whole_request_and_requires_restatement(routing):
    cat, lex, policy = routing
    state = pending_dates(routing)
    state['request']['question'] = 'x'*50000
    state = compact(state, dict(status='partial', sources=[]))
    assert len(json.dumps(state).encode()) < 48*1024
    plan, _ = plan_turn('Workit', state, cat, lex, policy=policy)
    assert plan.clarification_reason == 'context_needs_restatement'


def test_number_cannot_bypass_missing_routing_slot(routing):
    cat, lex, policy = routing
    root = 'Thao tác trên phần mềm để xin nghỉ phép 2 ngày cần làm gì'
    _, pending = plan_turn(root, {}, cat, lex, policy=policy)
    plan, state = plan_turn('1', pending, cat, lex, policy=policy)
    assert plan.clarification and plan.missing_slots == ('system',)
    assert state['request']['question'] == root
    assert 'selected_task' not in state['request']


def test_http_date_followup_rechecks_sources_and_keeps_user_input_provenance(sample, monkeypatch):
    spec = ContextSpec(mode='branch', groups=['work'], facts=['employee_name'],
                       prompts={'as_of': 'Ngày xét: bạn muốn xét đến ngày nào?'}, note='Fictional')
    card = sample.catalog.cards[0].model_copy(update={'context': spec})
    sample.catalog = sample.catalog.model_copy(update={'cards': [card]})
    owner = service(sample)
    owner.settings = replace(owner.settings, conversation=ConversationSettings(True))
    owner.context.settings = TypeBSettings(True)
    owner.context.available = True
    reads = AsyncMock(side_effect=[snapshot(employee_name='First fictional name'),
                                    snapshot(employee_name='Second fictional name')])
    owner.context.employee = SimpleNamespace(read_context=reads, close=AsyncMock())
    store = MemoryStore()
    monkeypatch.setattr('src.conversation.storage.ConversationStore', lambda: store)
    room, actor = str(uuid4()), str(uuid4())

    def send(client, text):
        response = client.post('/api/v1/answer', json=dict(user_id=actor, hrm_user_id=7,
            room_id=room, message_id=str(uuid4()), expected_context_version=store.version_number,
            text=text, top_k=5))
        assert response.status_code == 200
        return response.json()['result']

    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=SERVICE_HEADERS) as client:
        first = send(client, 'Sinh con của tôi được bao nhiêu?')
        assert first['application']['missing_inputs'][0]['slot'] == 'as_of'
        assert {item['kind'] for item in first['evidence']} == {'document', 'employee'}
        client.portal.call(lambda: client.app.state.cache_manager.clear(namespace='conversation-context-v2'))
        # The published evidence is now missing. The previous citation must not reappear.
        owner.retrieval.read_evidence.return_value = {**sample.result, 'matches': []}
        second = send(client, '01/12/2026')
        assert second['status'] == 'partial' and not second['sources']
        assert not second['application']['missing_inputs']
        assert {(item['kind'], item['value']) for item in second['evidence']} == {
            ('user_input', '2026-12-01'), ('employee', 'Second fictional name')}
    assert owner.retrieval.read_evidence.await_count == 2
    assert all(call.args == (7, ('employee_name',)) and call.kwargs == {'manager': False}
               for call in reads.await_args_list)



@pytest.mark.parametrize(('question', 'label', 'aspect', 'clarification'), [
    ('Công ty quy định công tác phí như thế nào?', None, 'overview', None),
    ('Chế độ hoàn trả công tác phí khi thanh toán trước như thế nào như thế nào?', 'thanh toán công tác phí', 'overview', None),
    ('Chế độ hoàn trả công tác phí như thế nào?', 'thanh toán công tác phí', 'overview', None),
    ('tôi đã chi trả 2 triệu khi gặp đối tác thì làm sao để hoàn trả', None, 'procedure', 'chi phí tiếp khách'),
    ('Thủ tục thanh toán công tác phí gồm những bước nào?', 'thanh toán công tác phí', 'procedure', None),
    ('Thanh toán công tác phí cần chứng từ gì?', 'thanh toán công tác phí', 'paperwork', None),
    ('Công ty ứng tiền đi công tác thế nào?', 'tạm ứng công tác phí', 'overview', None),
    ('Tự bỏ tiền đi công tác, cần giấy tờ gì?', 'thanh toán công tác phí', 'paperwork', None),
    ('Quên bấm vân tay lúc tan làm thì xử lý thế nào?', 'quên chấm công', 'procedure', None),
    ('Quên quẹt vân tay lúc vào ca thì xử lý thế nào?', 'quên chấm công', 'procedure', None),
    ('Lỡ không bấm vân tay thì phải làm gì?', 'quên chấm công', 'procedure', None),
    ('Quên bấm máy chấm công thì xử lý thế nào?', 'quên chấm công', 'procedure', None),
    ('Tôi quên quẹt thẻ chấm công, giờ phải làm gì?', 'quên chấm công', 'procedure', None),
    ('Quên bấm công cuối ngày cần làm gì?', 'quên chấm công', 'procedure', None),
    ('Quên chấm công thì làm sao bổ sung?', 'quên chấm công', 'procedure', None),
    ('Lỡ quên quét vân tay thì xử lý thế nào?', 'quên chấm công', 'procedure', None),
    ('Tôi mới có em bé thì công ty hỗ trợ gì?', None, 'overview', 'nhân viên trực tiếp sinh con'),
    ('Phúc lợi sinh con là gì?', None, 'overview', 'nhân viên trực tiếp sinh con'),
    ('Mới đón con thì có trợ cấp gì?', None, 'overview', 'nhân viên trực tiếp sinh con'),
    ('Có con nhỏ thì công ty hỗ trợ gì?', None, 'overview', 'nhân viên trực tiếp sinh con'),
    ('Nhân viên nữ sinh con nhận bao nhiêu tiền?', 'Khoản hỗ trợ nhân viên nữ sinh con', 'amount', None),
    ('Hỗ trợ sinh con cần chứng từ gì?', 'Khoản hỗ trợ nhân viên nữ sinh con', 'paperwork', None),
    ('Hỗ trợ của công ty cho nhân viên nữ mới sinh?', 'Khoản hỗ trợ nhân viên nữ sinh con', 'overview', None),
    ('Mới sinh em bé thì công ty hỗ trợ gì?', 'Khoản hỗ trợ nhân viên nữ sinh con', 'overview', None),
    ('Muốn mượn ô tô của công ty đi gặp khách thì đăng ký ở đâu?', 'sử dụng xe tập đoàn', 'procedure', None),
    ('Xin dùng xe cơ quan cần làm gì?', 'sử dụng xe tập đoàn', 'procedure', None),
    ('Đi công việc thì xin mượn xe cơ quan như thế nào?', 'sử dụng xe tập đoàn', 'overview', None),
    ('Thủ tục mượn ô tô công ty là gì?', 'sử dụng xe tập đoàn', 'procedure', None),
    ('Dùng ô tô của công ty thì điều kiện là gì?', 'sử dụng xe tập đoàn', 'conditions', None),
    ('Tôi muốn đăng ký mượn xe công ty thì làm sao?', 'sử dụng xe tập đoàn', 'procedure', None),
    ('Mượn xe của công ty cần giấy tờ gì?', 'Biểu mẫu quản lý xe', 'paperwork', None),
    ('Thủ tục đăng ký sử dụng xe công ty', 'sử dụng xe tập đoàn', 'procedure', None),
])
def test_priority_policy_questions_route_by_intent(routing, question, label, aspect, clarification):
    catalog, lexicon, policy = routing
    plan, _ = plan_turn(question, {}, catalog, lexicon, policy=policy)
    assert {card.label for card in plan.cards} == ({label} if label else set())
    assert plan.aspects == (aspect,)
    if label:
        assert all(aspect in card.sections for card in plan.cards)
    if clarification:
        assert clarification in plan.clarification
        assert not plan.choice_options
    elif not label:
        assert plan.topic_unmatched
    else:
        assert not plan.clarification or plan.check_scopes


@pytest.mark.parametrize('question', [
    'Tôi mua vé xem phim và muốn hoàn tiền.',
    'Tôi quên bấm vân tay để mở khóa điện thoại cá nhân.',
    'Cách xin hỗ trợ sửa điện thoại cá nhân?',
    'Tôi có thể mượn ô tô của bạn tôi không?',
])
def test_priority_policy_clues_do_not_retrieve_unrelated_topics(routing, question):
    import asyncio
    from src.answering.discovery import suggest_document_topics

    catalog, lexicon, policy = routing
    retrieval = SimpleNamespace(policy=policy, discover=AsyncMock(), read_evidence=AsyncMock())
    plan, _ = plan_turn(question, {}, catalog, lexicon, policy=policy)
    resolved, _ = asyncio.run(suggest_document_topics(plan, catalog, retrieval))
    assert not resolved.cards and not resolved.choice_options and not resolved.document_lookup
    assert not retrieval.discover.called and not retrieval.read_evidence.called


def test_priority_topics_conversation_offers_current_options_and_handles_followups(sample, routing, monkeypatch):
    from src.evaluation.questions import FixtureRetrieval, fixture_records

    catalog, lexicon, policy = routing
    documents = FixtureRetrieval(policy, fixture_records(REPOSITORY_ROOT))
    owner = service(sample)
    owner.answers.catalog, owner.answers.lexicon = catalog, lexicon
    owner.retrieval.policy = policy
    owner.retrieval.discover = AsyncMock(side_effect=documents.discover)
    owner.retrieval.read_evidence = AsyncMock(side_effect=documents.read_evidence)
    owner.context._lookup_employee = AsyncMock(return_value=(snapshot(job_title='Fictional specialist'), 'applied'))
    owner.settings = replace(owner.settings, conversation=ConversationSettings(True))
    store = MemoryStore()
    monkeypatch.setattr('src.conversation.storage.ConversationStore', lambda: store)
    room, actor = str(uuid4()), str(uuid4())

    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=SERVICE_HEADERS) as client:
        def send(text, **extra):
            message = str(uuid4())
            response = client.post('/api/v1/answer', json=dict(user_id=actor, hrm_user_id=7,
                room_id=room, message_id=message, expected_context_version=store.version_number, text=text, **extra))
            assert response.status_code == 200, response.text
            return message, response.json()['result']

        prompt_id, prompt = send('Công ty quy định công tác phí như thế nào?')
        options = {row['label']: row['id'] for row in prompt['clarification_options']}
        assert options.keys() == {'tạm ứng công tác phí', 'phụ cấp công tác phí', 'thanh toán công tác phí'}
        assert not prompt['claims'] and not prompt['sources']
        before = owner.retrieval.read_evidence.await_count
        _, chosen = send('thanh toán công tác phí',
            choice={'prompt_message_id': prompt_id, 'option_id': options['thanh toán công tác phí']})
        assert chosen['claims'] and owner.retrieval.read_evidence.await_count > before
        _, paperwork = send('Cần chứng từ gì?')
        assert any('Điều 8.3' in item.get('quote', '') for item in paperwork['evidence'])
        assert len(paperwork['claims']) == 1
        _, vehicle = send('Muốn mượn ô tô của công ty đi gặp khách thì đăng ký ở đâu?')
        assert vehicle['claims'] and store.delta['task_id'] == 'task.business.vehicle-use.b114fe60'
        _, mixed = send('Chức danh của tôi là gì và công ty quy định công tác phí như thế nào?')
        assert mixed['status'] == 'partial' and mixed['fallback_reason'] == 'document_needs_clarification'
        assert {item['field'] for item in mixed['evidence']} == {'job_title'}
        assert 'Về phần chính sách' in mixed['answer']
        _, benefit = send('Tôi mới có em bé thì công ty hỗ trợ gì?')
        assert benefit['status'] == 'needs_clarification'
        assert 'nhân viên trực tiếp sinh con' in benefit['answer'] and not benefit['claims']
        _, attendance = send('Quên bấm vân tay lúc tan làm thì xử lý thế nào?')
        assert attendance['claims'] and store.delta['task_id'] == 'task.business.forgot-clock.c9973704'
        assert owner.context._lookup_employee.await_count == 1
        partial_evidence = client.post('/api/v1/retrieve', json={'hrm_user_id': 7,
            'text': 'Chức danh của tôi là gì và công ty quy định công tác phí như thế nào?'})
        assert partial_evidence.status_code == 200
        body = partial_evidence.json()
        assert body['status'] == 'partial_evidence' and not body['matches']
        assert {item['field'] for item in body['evidence']} == {'job_title'}


@pytest.mark.parametrize(('reason', 'fragment'), [
    ('no_eligible_documents', 'điều kiện truy cập và lập chỉ mục'),
    ('no_candidates', 'chưa tìm được đoạn tài liệu'),
])
def test_explicit_lookup_keeps_empty_reason_and_accurate_copy(sample, reason, fragment):
    from src.retrieval.reranking import relevance

    owner = service(sample)
    owner.retrieval.retrieve = AsyncMock(return_value={
        'status': 'no_eligible_documents' if reason == 'no_eligible_documents' else 'no_matches',
        'matches': [], 'fallback_reason': reason, 'relevance': relevance([])})
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=SERVICE_HEADERS) as client:
        response = client.post('/api/v1/answer', json={'text': 'Tìm tài liệu công tác phí', 'hrm_user_id': 7})
    assert response.status_code == 200
    row = response.json()
    assert row['status'] == 'insufficient_evidence'
    assert row['fallback_reason'] == reason and fragment in row['answer']
    assert not row['claims'] and not row['sources']
