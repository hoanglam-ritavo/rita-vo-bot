"""Request completeness and identity boundaries using fictional source evidence."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.contracts import ApiError
from src.answering.contextual import prepare, requirements
from src.answering.pipeline import render
from src.conversation.context import reduce_question
from src.tests.test_answering import sample, service
from src.tests.http_fixtures import SERVICE_HEADERS
from src.type_b.config import TypeBSettings
from src.type_b.employee_store.errors import EmployeeSourceError


@pytest.mark.parametrize('question,status', [
    ('Mã nhân viên của Lan là gì? Tôi muốn biết.', 'unsupported'),
    ('Ngày chính thức của quản lý trực tiếp của tôi là ngày nào?', 'unsupported'),
    ('Chức danh của Lan là gì và phòng ban của tôi?', 'unsupported'),
    ('Chức danh của tôi là gì và số dư phép còn bao nhiêu?', 'needs_clarification'),
    ('Chức danh của tôi kèm yêu cầu chưa rõ?', 'needs_clarification'),
    ('Tôi không muốn xem mã nhân viên', 'needs_clarification'),
    ('Sinh con được bao nhiêu và yêu cầu chưa rõ?', 'needs_clarification'),
])
def test_unresolved_or_other_subject_never_reads_profile_or_sources(sample, question, status):
    owner = service(sample)
    owner.context.employee = SimpleNamespace(read_context=AsyncMock(), close=AsyncMock())
    owner.context.available = True
    owner.answers.document_evidence = AsyncMock()
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=SERVICE_HEADERS) as client:
        for endpoint in ('answer', 'retrieve'):
            response = client.post('/api/v1/' + endpoint, json={'hrm_user_id': 7, 'text': question})
            assert response.status_code == 200
            assert response.json()['status'] == status
            assert response.json()['evidence'] == []
    owner.context.employee.read_context.assert_not_awaited()
    owner.answers.document_evidence.assert_not_awaited()


def test_recognized_policy_is_not_dropped_from_mixed_request(sample):
    plan = prepare('Chức danh của tôi là gì và chế độ sinh con được bao nhiêu?', sample.catalog, sample.lexicon)
    assert plan.card.id == 'birth'
    assert plan.direct_fields == ('job_title',)
    assert not plan.clarification


def test_manager_email_only_requests_the_email_projection():
    plan = prepare('Email quản lý trực tiếp của tôi là gì?')
    assert plan.direct_fields == ('manager_email',)
    assert requirements(plan) == ((), True)


def test_authored_conjunction_and_labelled_inputs_are_not_new_requests(sample):
    card = sample.catalog.cards[0].model_copy(update={'aliases': ['cài đặt và cập nhật phần mềm'], 'concept_ids': []})
    catalog = sample.catalog.model_copy(update={'cards': [card]})
    plan = prepare('Về cài đặt và cập nhật phần mềm, các bước thực hiện là gì?', catalog, sample.lexicon)
    assert plan.card.id == card.id and not plan.clarification
    plan = prepare('Sinh con; Lịch làm việc: chức danh của Lan và phòng ban của tôi', sample.catalog, sample.lexicon)
    assert not plan.unsupported_person and not plan.direct_fields and not plan.clarification


def test_mixed_http_answer_contains_both_requested_parts(sample):
    from src.tests.test_contextual_answering import snapshot
    owner = service(sample)
    owner.context.settings = TypeBSettings(True)
    owner.context.available = True
    owner.context.employee = SimpleNamespace(read_context=AsyncMock(return_value=snapshot(job_title='Synthetic analyst')),
                                             close=AsyncMock())
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=SERVICE_HEADERS) as client:
        response = client.post('/api/v1/answer', json={'hrm_user_id': 7,
            'text': 'Chức danh của tôi là gì và sinh con được bao nhiêu?'})
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'answered'
    assert 'Synthetic analyst' in data['answer'] and '100 credits' in data['answer']
    assert {item['kind'] for item in data['evidence']} == {'employee', 'document'}
    owner.context.employee.read_context.assert_awaited_once_with(7, ('job_title',), manager=False)


def test_document_lookup_does_not_request_profile_and_allows_multiple_topics(sample):
    plan = prepare('Tìm tài liệu hướng dẫn cập nhật chức danh của tôi', sample.catalog, sample.lexicon)
    assert plan.analysis.document_lookup
    assert requirements(plan) == ((), False)
    second = sample.catalog.cards[0].model_copy(update={'id': 'second'})
    catalog = sample.catalog.model_copy(update={'cards': [*sample.catalog.cards, second]})
    row = render('Tìm tài liệu về sinh con', sample.result, catalog, sample.lexicon)
    assert row['reason'] == 'document_lookup'
    assert row['claims'] and row['status'] == 'partial'


def test_document_lookup_http_uses_retrieval_without_profile(sample):
    owner = service(sample)
    employee = SimpleNamespace(read_context=AsyncMock(), close=AsyncMock())
    owner.context.employee, owner.context.available = employee, True
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=SERVICE_HEADERS) as client:
        data = client.post('/api/v1/answer', json={'hrm_user_id': 7,
            'text': 'Tìm tài liệu hướng dẫn cập nhật chức danh của tôi'}).json()
    assert data['fallback_reason'] == 'document_lookup' and data['claims']
    assert all(item['kind'] == 'document' for item in data['evidence'])
    employee.read_context.assert_not_awaited()
    owner.retrieval.retrieve.assert_awaited_once()


def test_numbered_selection_preserves_aspect_and_rejects_unrelated_input(sample):
    first = sample.catalog.cards[0].model_copy(update={
        'aliases': ['quyền lợi A', 'sự kiện chung'], 'concept_ids': []})
    second = first.model_copy(update={'id': 'second', 'label': 'Fictional B', 'aliases': ['quyền lợi B', 'sự kiện chung']})
    catalog = sample.catalog.model_copy(update={'cards': [first, second]})
    _, pending = reduce_question('Hồ sơ sự kiện chung?', {}, catalog, sample.lexicon)
    assert pending['candidates'] == [first.id, second.id]
    assert pending['requested_aspects'] == ['paperwork']
    question, selected = reduce_question('2', pending, catalog, sample.lexicon)
    assert selected['task_id'] == second.id
    assert question == 'quyền lợi B hồ sơ giấy tờ'
    question, state = reduce_question('không chọn quyền lợi B', pending, catalog, sample.lexicon)
    assert question == 'không chọn quyền lợi B' and state['task_id'] is None
    question, state = reduce_question('9', pending, catalog, sample.lexicon)
    assert question == 'Hồ sơ sự kiện chung?' and state['candidates'] == pending['candidates']


def test_employee_source_recovers_after_startup_failure_with_cooldown(sample, monkeypatch):
    async def scenario():
        owner = service(sample)
        owner.context.settings = TypeBSettings(True)
        employee = SimpleNamespace(check_source=AsyncMock(side_effect=[EmployeeSourceError('source_unavailable'), None]),
                                   close=AsyncMock())
        owner.context.employee = employee
        await owner.context.start()
        assert not owner.context.available
        await owner.context.check_source()
        assert employee.check_source.await_count == 1
        owner.context._next_source_check = 0
        await asyncio.gather(owner.context.check_source(), owner.context.check_source())
        assert owner.context.available and employee.check_source.await_count == 2
    asyncio.run(scenario())


def test_disabled_employee_source_never_rechecks_or_reads(sample):
    async def scenario():
        owner = service(sample)
        employee = SimpleNamespace(check_source=AsyncMock(), read_context=AsyncMock())
        owner.context.employee = employee
        owner.context.available = False
        await owner.context.check_source()
        employee.check_source.assert_not_awaited()
    asyncio.run(scenario())


def test_conversation_limits_are_transaction_local(monkeypatch):
    from contextlib import asynccontextmanager
    from src.conversation import storage
    async def scenario():
        db = SimpleNamespace(execute=AsyncMock())
        @asynccontextmanager
        async def scope():
            yield db
        monkeypatch.setattr(storage, 'session_scope', scope)
        async with storage.conversation_scope() as current:
            assert current is db
        statements = [str(call.args[0]) for call in db.execute.await_args_list]
        assert statements == ["SET LOCAL lock_timeout = '5s'", "SET LOCAL statement_timeout = '15s'"]
    asyncio.run(scenario())


@pytest.mark.parametrize('stage', ['begin', 'context', 'finish'])
def test_whole_conversation_timeout_includes_storage(monkeypatch, stage):
    from src.tests.test_conversation import setup_service
    from src.conversation.service import ConversationService
    from src.helper.cache import CacheManager
    async def scenario():
        request, owner, store = setup_service()
        async def blocked(*args, **kwargs):
            await asyncio.Event().wait()
        getattr(store, stage).side_effect = blocked
        cache = CacheManager()
        svc = ConversationService(owner, cache, store)
        monkeypatch.setattr('src.conversation.service.DEADLINE_SECONDS', .01)
        with pytest.raises(ApiError) as error:
            await svc.answer(request)
        assert error.value.code == 'answer_timeout' and error.value.status_code == 504
        assert not svc.active
        if stage == 'begin':
            store.finish.assert_not_awaited()
        else:
            assert store.fail.await_args.args[2:] == ('FAILED', 'answer_timeout')
        await cache.close()
    asyncio.run(scenario())
