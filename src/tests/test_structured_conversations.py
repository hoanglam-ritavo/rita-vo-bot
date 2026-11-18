"""Actual HTTP/coordinator/reducer paths with fictional evidence and durable memory."""
from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.contracts import ApiError
from src.answering.catalog import load_catalog
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
        pass

    async def begin(self, request, *, retry=False):
        key = request.message_id
        if key in self.turns:
            if self.payloads[key] != request.model_dump():
                raise ApiError('message_conflict', 409)
            return self.turns[key], False
        if request.expected_context_version != self.version_number:
            raise ApiError('context_conflict', 409)
        self.payloads[key] = deepcopy(request.model_dump())
        self.turns[key] = TurnResponse(room_id=request.room_id, message_id=key, sequence=len(self.turns)+1,
            state='PROCESSING', attempt=1, context_version=self.version_number)
        return self.turns[key], True

    async def version(self, *args):
        return self.version_number

    async def context(self, *args):
        return self.version_number, deepcopy(self.delta), []

    async def finish(self, request, attempt, result, delta):
        self.version_number += 1
        self.delta = deepcopy(delta)
        value = self.turns[request.message_id].model_copy(update={
            'state':'COMPLETED', 'context_version':self.version_number, 'result':result})
        self.turns[request.message_id] = value
        return value

    async def fail(self, *args):
        raise AssertionError('Unexpected failure in fictional conversation')


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
        assert owner.retrieval.read_evidence.await_count == 1
        again = client.post('/api/v1/answer', json=final_body)
        assert again.json()['result'] == final.json()['result']
        assert owner.retrieval.read_evidence.await_count == 1


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
