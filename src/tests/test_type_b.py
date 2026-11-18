"""Fictional HRM HTTP contracts and ownership; no .env, database or model access."""
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
from src.database.runtime import run_async
from src.employee_source.contracts import PROFILE_FIELDS, normalize_profile
from src.employee_source.errors import EmployeeValidationError, EmployeeSourceError
from src.retrieval import RetrievalService, RetrievalError
from src.retrieval import storage
from src.type_b.config import TypeBSettings, from_environment
from src.type_b.tasks import TASKS, route_question

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
    settings = Settings(Path('unused'), 'a'*40, type_b=TypeBSettings(enabled, KEY if enabled else None),
                        answer=AnswerSettings(enabled=True))
    owner = ApiService(settings, retrieval=retrieval, employee_service=employee,
                       database_check=AsyncMock(), database_close=AsyncMock())
    return owner, employee


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('task,question', list(zip(TASKS, [
    'Họ tên của tôi là gì?', 'Mã nhân viên của tôi là gì?', 'Chức danh của tôi là gì?',
    'Phòng ban của tôi là gì?', 'Quản lý trực tiếp của tôi là ai?',
    'Ngày bắt đầu thử việc của tôi?', 'Ngày kết thúc thử việc của tôi?', QUESTION, 'Ngày nghỉ việc của tôi?',
])))
def test_supported_fields_by_label_value_and_no_personnel_leak(endpoint, task, question, caplog):
    owner, employee = service()
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        response = client.post(endpoint, json=dict(text=question, hrm_user_id=7), headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    data = response.json()
    assert data['source_type'] == 'type_b'
    assert data['employee_evidence']['facts'] == [dict(ref_id='B1', field=task.field, label=task.label,
                                                     value=getattr(profile(), task.field))]
    assert set(data['employee_evidence']['source']) == {'relation', 'observed_at', 'updated_at'}
    if endpoint.endswith('answer'):
        assert data['status'] == 'answered'
        assert data['claims'] == data['sources'] == []
        assert data['employee_claims'][0]['fact_refs'] == ['B1']
        if task.field == 'official_date':
            assert '02/02/2020' in data['answer']
    else:
        assert data['status'] == 'evidence_found' and data['matches'] == []
    assert 'restricted-marker' not in response.text + caplog.text
    assert KEY not in response.text + caplog.text
    assert question not in caplog.text
    assert 'hrm_user_id' not in response.text
    employee.find_by_user_id.assert_awaited_once_with(7)
    owner.retrieval.retrieve.assert_not_awaited()
    owner.retrieval.retrieve_prepared.assert_not_awaited()
    employee.close.assert_awaited_once()


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('value', [True, False, 0, -1, 1.5, '7', 2**53])
def test_strict_reference_types(endpoint, value):
    owner, employee = service()
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        result = client.post(endpoint, json=dict(text=QUESTION, hrm_user_id=value), headers=HEADERS)
        assert result.status_code == 422
        assert result.json()['error']['code'] == 'invalid_request'
        result = client.post(endpoint, json=dict(text=QUESTION, employee_id=42), headers=HEADERS)
        assert result.status_code == 422
    employee.find_by_user_id.assert_not_awaited()


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('case,status,code', [
    ('no_key', 401, 'service_authentication_required'), ('wrong_key', 401, 'service_authentication_required'),
    ('no_id', 422, 'hrm_user_id_required'), ('disabled', 503, 'type_b_disabled')])
def test_caller_and_configuration_guards(endpoint, case, status, code):
    owner, employee = service(enabled=case != 'disabled')
    body = {'text': QUESTION} | ({} if case == 'no_id' else {'hrm_user_id': 7})
    headers = {} if case == 'no_key' else {'X-Rita-Service-Key': 'wrong' if case == 'wrong_key' else KEY}
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        result = client.post(endpoint, json=body, headers=headers)
    assert result.status_code == status, result.text
    assert result.json()['error']['code'] == code
    assert result.headers['cache-control'] == 'no-store'
    employee.find_by_user_id.assert_not_awaited()
    if case in ('no_key', 'wrong_key', 'disabled'):
        owner.retrieval.prepare_query.assert_not_awaited()


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('question,reason', [
    ('Ngày vào làm của tôi?', 'ambiguous_employment_date'),
    ('Phòng ban của anh Nam là gì?', 'other_employee_unsupported'),
    ('Phòng ban của tôi và số tài khoản ngân hàng của tôi?', 'employee_task_unsupported'),
    ('Toàn bộ hồ sơ của tôi?', 'employee_task_unsupported'),
    ('Quản lý trực tiếp là ai?', 'employee_reference_unclear'),
    ('Phòng ban của tôi và một thông tin khác?', 'employee_task_unclear'),
])
def test_no_reads_for_ambiguous_or_unsupported_tasks(endpoint, question, reason):
    owner, employee = service()
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        result = client.post(endpoint, json=dict(text=question, hrm_user_id=7), headers=HEADERS)
    assert result.status_code == 200
    assert result.json()['employee_evidence']['reason'] == reason
    assert result.json()['employee_evidence']['facts'] == []
    employee.find_by_user_id.assert_not_awaited()
    owner.retrieval.retrieve_prepared.assert_not_awaited()




@pytest.mark.parametrize('endpoint', ENDPOINTS)
def test_null_missing_and_partial_are_distinct(endpoint):
    owner, employee = service(personnel=profile(official_date=None))
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        result = client.post(endpoint, json=dict(text=QUESTION, hrm_user_id=7), headers=HEADERS).json()
        assert result['status'] == ('no_evidence' if endpoint.endswith('retrieve') else 'insufficient_evidence')
        assert result['employee_evidence']['facts'][0]['value'] is None
        result = client.post(endpoint, json=dict(text='Ngày chính thức và phòng ban của tôi?', hrm_user_id=7), headers=HEADERS).json()
        assert result['status'] == ('partial_evidence' if endpoint.endswith('retrieve') else 'partial')
        employee.find_by_user_id.return_value = None
        result = client.post(endpoint, json=dict(text=QUESTION, hrm_user_id=7), headers=HEADERS).json()
        assert result['employee_evidence']['status'] == 'not_found'
        assert result['employee_evidence']['reason'] == 'employee_not_found'


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('error,status', [
    (EmployeeSourceError('source_timeout'), 504), (EmployeeValidationError('conflicting_rows'), 503),
    (RuntimeError('restricted-marker'), 503)])
def test_source_failures_never_fall_back_to_policy(endpoint, error, status, caplog):
    owner, employee = service()
    employee.find_by_user_id.side_effect = error
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        result = client.post(endpoint, json=dict(text=QUESTION, hrm_user_id=7), headers=HEADERS)
    assert result.status_code == status
    assert 'restricted-marker' not in result.text + caplog.text
    employee.find_by_user_id.assert_awaited_once()
    owner.retrieval.retrieve_prepared.assert_not_awaited()


def test_type_a_preserves_shape_without_reading_employee_even_with_reference():
    owner, employee = service()
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        for body, headers in [({'text': 'Chính sách nghỉ phép là gì?'}, {}),
                              ({'text': 'Chính sách nghỉ phép là gì?', 'hrm_user_id': 7}, HEADERS),
                              ({'text': 'Chính sách nghỉ phép là gì?', 'hrm_user_id': None}, {})]:
            result = client.post('/api/v1/retrieve', json=body, headers=headers)
            assert result.status_code == 200
            assert set(result.json()) == {'status', 'matches', 'fallback_reason', 'relevance', 'timings'}
    employee.find_by_user_id.assert_not_awaited()


def test_mixed_retrieval_keeps_document_and_personnel_evidence_separate():
    owner, employee = service()
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        result = client.post('/api/v1/retrieve', json=dict(
            text='Ngày chính thức của tôi và chính sách trợ cấp?', hrm_user_id=7), headers=HEADERS)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['source_type'] == 'mixed' and data['status'] == 'evidence_found'
    assert data['matches'][0]['content'] == 'Fictional policy evidence.'
    assert data['employee_evidence']['facts'][0]['field'] == 'official_date'
    owner.retrieval.retrieve_prepared.assert_awaited_once()
    employee.find_by_user_id.assert_awaited_once()


def test_mixed_answer_is_partial_without_catalog_and_pure_b_still_answers():
    owner, _ = service()
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        result = client.post('/api/v1/answer', json=dict(
            text='Ngày chính thức của tôi và chính sách trợ cấp?', hrm_user_id=7), headers=HEADERS)
    data = result.json()
    assert data['status'] == 'partial' and data['employee_claims']
    assert data['claims'] == []
    assert 'chưa có kết luận' in data['answer']
    assert data['fallback_reason'] == 'mixed_individual_eligibility_not_evaluated'


def test_mixed_answer_with_real_template_renderer_keeps_both_reference_kinds():
    import hashlib
    from src.answering.catalog import Catalog, METADATA
    owner, _ = service()
    match = documents()['matches'][0]
    reference = dict(source_sha256='a'*64, document_id=match['document_id'], revision_id=match['revision_id'],
        chunk_index=0, content_sha256=hashlib.sha256(match['content'].encode()).hexdigest(),
        quote=match['content'], metadata={key: match[key] for key in METADATA})
    catalog = Catalog.model_validate(dict(schema_version=2, package_fingerprint='fake', lexicon_sha256='fake',
        cards=[dict(source_type='type_a', clarification='Fictional clarification', id='policy', label='Fictional policy', concept_ids=[], aliases=['chính sách trợ cấp'],
            requires_any=[], excludes_any=[], sections={'overview': dict(complete=True,
                units=[dict(id='evidence.fictional', text='Fictional policy evidence.', refs=[reference])])})], coverage=[]))
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        owner.answers.available = True
        owner.answers.catalog = catalog
        owner.answers.lexicon = {'concepts': []}
        response = client.post('/api/v1/answer', json=dict(
            text='Ngày chính thức của tôi và chính sách trợ cấp?', hrm_user_id=7), headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['status'] == 'partial'
    assert data['claims'][0]['citations'][0]['source_id'] == 'S1'
    assert data['sources'][0]['origins'][0]['chunk_id'] == 'chunk'
    assert data['employee_claims'][0]['fact_refs'] == ['B1']
    assert 'Thông tin hồ sơ' in data['answer'] and 'Thông tin chính sách' in data['answer']
    assert 'chưa có kết luận' in data['answer']


@pytest.mark.parametrize('question,source', [
    ('Chính sách ngày chính thức của tôi?', 'type_a'),
    ('Phòng ban của Nguyễn Văn A, tôi muốn biết?', 'type_b'),
    ('Phòng ban Nguyễn Minh là gì?', 'type_b'),
    ('Tên quản lý trực tiếp của tôi là gì?', 'type_b'),
    ('Phòng ban của tôi và chính sách của công ty?', 'mixed'),
    ('Ngày chính thức của tôi, tôi có được trợ cấp không?', 'mixed'),
    ('Ngày chính thức của tôi và hướng dẫn sử dụng Workit?', 'mixed'),
])
def test_policy_and_named_subjects_are_not_treated_as_self_values(question, source):
    route = route_question(question)
    assert route.source == source
    if 'Nguyễn' in question:
        assert not route.fields and route.reason is not None
    if question.startswith('Tên quản lý'):
        assert route.fields == ('manager_name',)


def test_http_configuration_loads_type_b_without_logging_the_key(monkeypatch, tmp_path, caplog):
    from src.api import config
    monkeypatch.setattr(config, 'REPOSITORY_ROOT', tmp_path)
    (tmp_path / '.env').write_text(f'TYPE_B_ENABLED=true\nTYPE_B_SERVICE_KEY={KEY}\n', encoding='utf-8')
    monkeypatch.setenv('TYPE_B_ENABLED', 'false')
    monkeypatch.setenv('TYPE_B_SERVICE_KEY', 'different-fictional-key')
    settings = config.load_settings()
    assert settings.type_b.enabled and settings.type_b.service_key == KEY
    assert KEY not in caplog.text + repr(settings)




def test_request_deadline_releases_slot_without_retry(monkeypatch):
    from src.type_b import service as module
    async def exercise():
        owner, employee = service()
        finished = asyncio.Event()
        async def read(user_id):
            try:
                await asyncio.Future()
            finally:
                finished.set()
        employee.find_by_user_id.side_effect = read
        await owner.start()
        monkeypatch.setattr(module, 'DEADLINE_SECONDS', 0.02)
        with pytest.raises(ApiError, match='retrieval_timeout'):
            await owner.execute(QUESTION, 5, 7, KEY)
        assert finished.is_set() and not owner.busy and owner._idle.is_set()
        employee.find_by_user_id.assert_awaited_once()
        await owner.close()
    run_async(exercise())


def test_openapi_declares_optional_reference_and_service_header():
    schema = create_app().openapi()
    assert 'hrm_user_id' in schema['components']['schemas']['RetrieveRequest']['properties']
    assert 'hrm_user_id' not in schema['components']['schemas']['RetrieveRequest']['required']
    for path in ENDPOINTS:
        parameters = schema['paths'][path]['post']['parameters']
        assert any(p['name'] == 'X-Rita-Service-Key' and p['in'] == 'header' for p in parameters)
        assert 'anyOf' in schema['paths'][path]['post']['responses']['200']['content']['application/json']['schema']


def test_failed_hrm_preflight_keeps_type_a_available_and_closes_pool():
    owner, employee = service()
    employee.check_source.side_effect = RuntimeError('restricted-marker')
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        assert client.post('/api/v1/retrieve', json={'text': 'Chính sách nghỉ phép?'}).status_code == 200
        result = client.post('/api/v1/retrieve', json=dict(text=QUESTION, hrm_user_id=7), headers=HEADERS)
        assert result.status_code == 503
    employee.find_by_user_id.assert_not_awaited()
    employee.close.assert_awaited_once()


def test_feature_settings_are_strict_and_secret_repr_is_safe():
    assert not TypeBSettings().enabled
    assert KEY not in repr(from_environment({'TYPE_B_ENABLED': 'true', 'TYPE_B_SERVICE_KEY': KEY}))
    for values in ({'TYPE_B_ENABLED': 'yes'}, {'TYPE_B_ENABLED': 'true'},
                   {'TYPE_B_ENABLED': 'true', 'TYPE_B_SERVICE_KEY': ' '}):
        with pytest.raises(ValueError):
            from_environment(values)


def test_original_embedding_reused_without_changing_type_a_storage_or_fingerprints(monkeypatch):
    async def exercise():
        retrieval = RetrievalService(model_path=Path('unused'), model_revision='a'*40)
        retrieval._model = object()
        retrieval._fingerprint = 'model-fingerprint'
        retrieval._encode = Mock(return_value=[1.0] + [0.0]*383)
        search = AsyncMock(return_value=dict(status='no_matches', matches=[], corpus_fingerprint='corpus'))
        monkeypatch.setattr(storage, 'search', search)
        prepared = await retrieval.prepare_query('Original question', 5)
        result = await retrieval.retrieve_prepared(prepared, 5)
        retrieval._encode.assert_called_once_with('Original question')
        assert result['corpus_fingerprint'] == 'corpus'
        assert not hasattr(prepared, 'hint')
        assert search.call_args.args[1:3] == ('a'*40, 'model-fingerprint')
        await retrieval.close()
        retrieval._model = object()
        with pytest.raises(RetrievalError, match='invalid_prepared_query'):
            await retrieval.retrieve_prepared(prepared)
        await retrieval.close()
    run_async(exercise())


def test_cancellation_and_shutdown_keep_ownership_through_source_cleanup():
    async def exercise():
        owner, employee = service()
        entered, release = asyncio.Event(), asyncio.Event()
        async def read(user_id):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await release.wait()
        employee.find_by_user_id.side_effect = read
        await owner.start()
        task = asyncio.create_task(owner.execute(QUESTION, 5, 7, KEY))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert owner.busy
        with pytest.raises(ApiError, match='busy'):
            await owner.execute(QUESTION, 5, 7, KEY)
        closing = asyncio.create_task(owner.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await closing
        assert not owner.busy
        employee.close.assert_awaited_once()
    run_async(exercise())


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
