"""Fictional HRM HTTP contracts and ownership; no .env, database or model access."""
from src.tests.http_fixtures import SERVICE_KEY, SERVICE_HEADERS, http_service_environment
import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app, execute_until_disconnect
from src.api.config import Settings
from src.api.contracts import ApiError
from src.api.service import ApiService
from src.answering.config import AnswerSettings
from src.database.config import ConfigurationError
from src.database.runtime import run_async
from src.type_b.employee_store.fields import PROFILE_FIELDS
from src.type_b.employee_store.normalization import normalize_profile
from src.type_b.employee_store.errors import EmployeeValidationError, EmployeeSourceError
from src.retrieval import RetrievalService, RetrievalError
from src.retrieval import storage
from src.type_b.config import TypeBSettings, from_environment
from src.type_b.tasks import TASKS, route_question
from src.type_b.employee_store.contracts import EmployeeContext, ManagerResult, ManagerSummary
from types import MappingProxyType
from datetime import datetime, timezone

KEY = 'fictional-service-key'
HEADERS = {'X-Rita-Service-Key': KEY}
QUESTION = 'Ngày chính thức của tôi là ngày nào?'
ENDPOINTS = ['/api/v1/retrieve', '/api/v1/answer']
UNKNOWN = {'status': 'unavailable', 'source': None, 'styles': []}


def profile(**changes):
    data = dict.fromkeys(PROFILE_FIELDS)
    data.update(id=1, employee_id=42, user_id=7, updated_at='2026-11-17T10:00:00.123456+07:00',
        employee_name='Synthetic Employee', employee_code='CODE-007', job_title='Test role',
        department_name='A / B / C / D', manager_name='Test manager',
        probation_date='2020-01-01', probation_end_date='2020-02-01', official_date='2020-02-02',
        resignation_date='2021-01-01', bank_account_number='restricted-marker', notes='restricted-marker')
    data.update(changes)
    return normalize_profile(data)


def documents():
    return dict(status='matches_found', fallback_reason=None,
        relevance=dict(status='unassessed', warnings=['relevance_not_assessed']), matches=[dict(
            chunk_id='chunk', chunk_index=0, run_id='run', document_id='doc', revision_id='rev',
            content='Fictional policy evidence.', cosine_distance=0.1, rerank_score=1.0, title='Policy',
            author_id=None, role=None, source_path='fictional.txt', version_label=None,
            effective_from=None, effective_to=None, applicability='Fictional scope', source_spans=[])])


def service(*, enabled=True, hint=None, personnel=None):
    retrieval = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), started=True,
        retrieve=AsyncMock(return_value=documents()),
        read_evidence=AsyncMock(return_value={**documents(), 'matches':[{**documents()['matches'][0], 'source_sha256':'a'*64}]}),
        retrieve_prepared=AsyncMock(return_value=documents()),
        prepare_query=AsyncMock(side_effect=lambda text, top_k: SimpleNamespace(question=text, hint=hint or UNKNOWN)))
    employee = SimpleNamespace(check_source=AsyncMock(), close=AsyncMock(),
        find_by_user_id=AsyncMock(return_value=personnel if personnel is not None else profile()))
    settings = Settings(Path('unused'), 'a'*40, type_b=TypeBSettings(enabled),
                        answer=AnswerSettings(enabled=True), service_key=SERVICE_KEY)
    async def context(user_id, fields, *, manager=False):
        current = personnel if personnel is not None else profile()
        relation = ManagerResult('found', ManagerSummary(90, None, 'M90', 'Test manager', None, None, None, 'manager@example.test', None, True, None)) if manager else None
        return EmployeeContext(MappingProxyType({field:getattr(current,field) for field in fields}), relation,
                               datetime.now(timezone.utc).isoformat(), current.updated_at)
    employee.read_context = AsyncMock(side_effect=context)
    owner = ApiService(settings, retrieval=retrieval, employee_service=employee,
                       database_check=AsyncMock(), database_close=AsyncMock())
    return owner, employee


def test_http_configuration_loads_type_b_without_logging_the_key(monkeypatch, tmp_path, caplog):
    from src.api import config
    monkeypatch.setattr(config, 'REPOSITORY_ROOT', tmp_path)
    (tmp_path / '.env').write_text(f'TYPE_B_ENABLED=true\nCHAT_BOT_SERVICE_KEY={KEY}\n', encoding='utf-8')
    monkeypatch.setenv('TYPE_B_ENABLED', 'false')
    monkeypatch.setenv('TYPE_B_SERVICE_KEY', 'different-fictional-key')
    settings = config.load_settings()
    assert settings.type_b.enabled and settings.service_key == KEY
    assert KEY not in caplog.text + repr(settings)

def test_feature_settings_are_strict_and_secret_repr_is_safe():
    assert not TypeBSettings().enabled
    assert KEY not in repr(from_environment({'TYPE_B_ENABLED': 'true', 'TYPE_B_SERVICE_KEY': KEY}))
    assert from_environment({'TYPE_B_ENABLED': 'true', 'TYPE_B_SERVICE_KEY': ' '}).enabled
    with pytest.raises(ValueError):
        from_environment({'TYPE_B_ENABLED': 'yes'})

def test_disconnect_wrapper_cancels_and_drains_operation():
    async def exercise():
        completed = asyncio.Event()
        async def operation():
            try:
                await asyncio.Future()
            finally:
                completed.set()
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
        with pytest.raises(asyncio.CancelledError):
            await execute_until_disconnect(request, operation())
        assert completed.is_set()
    run_async(exercise())


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('task', TASKS)
def test_unified_facts_by_label_value_without_private_profile(endpoint, task, caplog):
    owner, employee = service()
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=HEADERS) as client:
        response = client.post(endpoint, json={'text': f'{task.label} của tôi là gì?', 'hrm_user_id': 7})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['schema_version'] == 2 and 'source_type' not in data and 'employee_claims' not in data
    fact = next(e for e in data['evidence'] if e['kind'] == 'employee')
    expected = 'Test manager' if task.field == 'manager_name' else getattr(profile(), task.field)
    assert (fact['field'], fact['label'], fact['value']) == (task.field, task.label, expected)
    assert data['application']['state'] == 'applied'
    if endpoint.endswith('answer'):
        assert data['status'] == 'answered'
        assert data['claims'][0]['evidence_refs'] == [fact['id']]
        assert 'Thông tin hồ sơ' not in data['answer'] and 'Thông tin chính sách' not in data['answer']
    else:
        assert data['status'] == 'evidence_found' and data['matches'] == []
    assert 'restricted-marker' not in response.text + caplog.text
    assert KEY not in response.text + caplog.text and 'hrm_user_id' not in response.text
    employee.read_context.assert_awaited_once()
    owner.retrieval.retrieve_prepared.assert_not_awaited()
    employee.close.assert_awaited_once()


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('value', [None, True, False, 0, -1, 1.5, '7', 2**53])
def test_required_strict_identity_before_reads(endpoint, value):
    owner, employee = service()
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=HEADERS) as client:
        for body in ({'text': QUESTION}, {'text': QUESTION, 'hrm_user_id': value}, {'text': QUESTION, 'employee_id': 42}):
            assert client.post(endpoint, json=body).status_code == 422
    employee.read_context.assert_not_awaited()


@pytest.mark.parametrize('endpoint', ENDPOINTS)
def test_service_auth_required_even_for_general_documents(endpoint):
    owner, employee = service()
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        for headers in ({}, {'X-Rita-Service-Key':'wrong'}):
            assert client.post(endpoint, json={'text': 'Quy định chung?', 'hrm_user_id':7}, headers=headers).status_code == 401
    employee.read_context.assert_not_awaited()


@pytest.mark.parametrize('error', [EmployeeSourceError('source_timeout'), EmployeeValidationError('conflicting_rows'), EmployeeSourceError('source_access_denied')])
def test_personnel_failure_is_explicit_and_does_not_fabricate_facts(error, caplog):
    owner, employee = service()
    employee.read_context.side_effect = error
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner), headers=HEADERS) as client:
        response = client.post('/api/v1/answer', json={'text': QUESTION,'hrm_user_id':7})
    data=response.json()
    assert data['status'] == 'insufficient_evidence' and not data['evidence']
    assert data['application']['state'] == 'unavailable' and data['limitations']
    employee.read_context.assert_awaited_once()
    assert KEY not in caplog.text and 'restricted-marker' not in caplog.text


def test_missing_profile_null_field_and_disabled_source_differ():
    owner, employee=service(personnel=profile(official_date=None))
    with TestClient(create_app(owner.settings, service_factory=lambda _:owner),headers=HEADERS) as client:
        data=client.post('/api/v1/answer',json={'text':QUESTION,'hrm_user_id':7}).json()
        assert data['evidence'][0]['value'] is None and data['application']['state']=='partial'
        employee.read_context.side_effect=None
        employee.read_context.return_value=None
        data=client.post('/api/v1/answer',json={'text':QUESTION,'hrm_user_id':7}).json()
        assert not data['evidence'] and data['application']['state']=='not_found'
    owner,_=service(enabled=False)
    with TestClient(create_app(owner.settings,service_factory=lambda _:owner),headers=HEADERS) as client:
        data=client.post('/api/v1/answer',json={'text':QUESTION,'hrm_user_id':7}).json()
        assert data['application']['state']=='disabled'


@pytest.mark.parametrize('enabled,read_error,lookup_state,application_state,reason,missing', [
    (True, None, 'applied', 'partial', '-', 'job_title'),
    (True, None, 'not_found', 'not_found', '-', '-'),
    (False, None, 'disabled', 'disabled', '-', '-'),
    (True, EmployeeValidationError('conflicting_rows'), 'unavailable', 'unavailable', 'conflicting_rows', '-'),
    (True, EmployeeSourceError('source_timeout'), 'unavailable', 'unavailable', 'source_timeout', '-'),
    (True, EmployeeSourceError('private-source-marker'), 'unavailable', 'unavailable', 'source_unavailable', '-'),
])
def test_employee_lookup_log_distinguishes_context_outcomes(
        enabled, read_error, lookup_state, application_state, reason, missing, caplog):
    owner, employee = service(enabled=enabled, personnel=profile(job_title=None))
    caplog.set_level('INFO', logger='src.type_b.service')
    message_id = '4a7565c6-0415-4c73-875b-35f14ee453e9'
    if read_error is not None or lookup_state == 'not_found':
        employee.read_context.side_effect = read_error
        employee.read_context.return_value = None

    async def scenario():
        await owner.start()
        try:
            return await owner.context.execute('Chức danh hiện tại của tôi là gì?', 5, 741852963,
                                               answer=True, message_id=message_id, attempt=1)
        finally:
            await owner.close()

    result = asyncio.run(scenario())
    assert result.application.state == application_state
    (lookup,) = [message for message in caplog.messages if message.startswith('employee_context_lookup ')]
    assert f'message_id={message_id} attempt=1' in lookup
    stage = 'read_context' if enabled else 'check_source'
    assert f'stage={stage} state={lookup_state} reason={reason} check_deferred=False' in lookup
    assert f'requested_fields=job_title missing_fields={missing}' in lookup
    assert '741852963' not in caplog.text and 'private-source-marker' not in caplog.text
    if not enabled:
        employee.check_source.assert_not_awaited()
        employee.read_context.assert_not_awaited()


@pytest.mark.parametrize('error,reason', [
    (ConfigurationError('private-configuration-marker'), 'invalid_source_configuration'),
    (EmployeeSourceError('schema_incompatible'), 'schema_incompatible'),
])
def test_employee_lookup_logs_deferred_reason_and_clears_it_on_recovery(error, reason, caplog):
    owner, employee = service()
    employee.check_source.side_effect = [error, None]
    caplog.set_level('INFO', logger='src.type_b.service')
    message_id = '4a7565c6-0415-4c73-875b-35f14ee453e9'

    async def scenario():
        await owner.start()
        try:
            failed = await owner.context.execute('Chức danh hiện tại của tôi là gì?', 5, 7,
                                                 answer=True, message_id=message_id, attempt=1)
            employee.read_context.assert_not_awaited()
            assert employee.check_source.await_count == 1
            owner.context._next_source_check = 0
            recovered = await owner.context.execute('Chức danh hiện tại của tôi là gì?', 5, 7,
                                                    answer=True, message_id=message_id, attempt=2)
            assert employee.check_source.await_count == 2
            return failed, recovered
        finally:
            await owner.close()

    failed, recovered = asyncio.run(scenario())
    assert failed.application.state == 'unavailable'
    assert recovered.application.state == 'applied'
    first, second = [message for message in caplog.messages if message.startswith('employee_context_lookup ')]
    assert f'attempt=1 stage=check_source state=unavailable reason={reason} check_deferred=True' in first
    assert 'attempt=2 stage=read_context state=applied reason=- check_deferred=False' in second
    assert 'private-configuration-marker' not in caplog.text


def test_general_retrieval_keeps_order_and_has_no_unnecessary_employee_reads():
    owner,employee=service()
    with TestClient(create_app(owner.settings,service_factory=lambda _:owner),headers=HEADERS) as client:
        data=client.post('/api/v1/retrieve',json={'text':'Quy định an toàn chung?','hrm_user_id':7}).json()
    assert data['schema_version']==2 and data['status']=='evidence_found'
    assert data['document_status']=='matches_found' and data['matches'][0]['chunk_id']=='chunk'
    assert data['application']['state']=='not_needed' and data['evidence']==[]
    employee.read_context.assert_not_awaited()
    owner.retrieval.prepare_query.assert_awaited_once()
    owner.retrieval.retrieve_prepared.assert_awaited_once()


def test_openapi_requires_id_and_only_unified_success_schema():
    owner,_=service()
    schema=create_app(owner.settings).openapi()
    assert 'hrm_user_id' in schema['components']['schemas']['RetrieveRequest']['required']
    assert 'ContextAnswerResponse' not in schema['components']['schemas']
    assert 'UnifiedAnswerResponse' in schema['components']['schemas']


def test_cancellation_drains_employee_read_and_releases_request_slot():
    async def exercise():
        owner,employee=service()
        owner.started=True
        owner.context.available=True
        entered=asyncio.Event()
        async def blocked(*args,**kwargs):
            entered.set()
            await asyncio.Event().wait()
        employee.read_context.side_effect=blocked
        task=asyncio.create_task(owner.execute(QUESTION,5,7,answer=True))
        await entered.wait()
        with pytest.raises(ApiError) as error:
            await owner.execute(QUESTION,5,7,answer=True)
        assert error.value.status_code==429
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert not owner.busy and owner._idle.is_set()
        await owner.close()
        employee.close.assert_awaited_once()
    run_async(exercise())
