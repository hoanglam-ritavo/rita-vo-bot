"""Harness-only regressions. No sockets, private dotenv, PostgreSQL or models."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from src.evaluation.http_e2e.config import (
    Configuration, EvaluationError, base_url, configuration, digest, private_path, write_private,
)
from src.evaluation.http_e2e.inventory import Scenario, build_inventory, public_inventory, select_profile
from src.evaluation.http_e2e.runner import prepare, run, request_for
from src.evaluation.http_e2e.snapshot import build_bindings, representatives, reference_inventory, reference_key
from src.evaluation.http_e2e.assertions import check_response


def config():
    return Configuration('http://127.0.0.1:8000', 'fictional-service-key',
        'postgresql+psycopg://reader:fictional@localhost/hrm_test',
        'postgresql+psycopg://reader:fictional@localhost/corpus_test')


def scenario(endpoint='/api/v1/answer', **overrides):
    oracle = {'http_status': 200, 'statuses': ['answered'], 'employee_fields': ['job_title'],
              'null_fields': [], 'fixture': 'current', 'claims': 'none'}
    oracle.update(overrides)
    return Scenario('q.employee.001', 'employee', endpoint, 'Chức danh của tôi là gì?', oracle)


def snapshot():
    row = dict(user_id=7, employee_id=42, employee_code='TEST42', employee_name='Fictional employee',
        job_title='Analyst', department_name='Test department', work_email='test@example.invalid',
        phone=None, avatar_url=None, active=True, updated_at='2026-11-18T00:00:00Z',
        manager_id=None, manager_code=None, official_date='2026-01-01')
    return {'employees': [row], 'documents': [], 'valid_references': [],
            'task_requirements': {}, 'fingerprint': digest(row), 'business_day': '2026-11-18'}


async def fake_snapshot(*args):
    return snapshot()


def answer_body(value='Analyst'):
    return {'schema_version': 2, 'status': 'answered', 'answer': 'Fictional job title [C1]',
        'claims': [{'id':'C1','text':'Fictional job title','evidence_refs':['F1']}], 'sources': [],
        'evidence': [{'kind':'employee','id':'F1','field':'job_title','subject':'self',
                     'label':'Chức danh','value':value,'observed_at':'2026-11-18T00:00:00Z','updated_at':None}],
        'application': {'state':'applied','mode':'personalized','missing_inputs':[],'conflicts':[]},
        'limitations': [], 'fallback_reason': None,
        'relevance': {'status':'no_candidates','warnings':['document_retrieval_not_requested']},
        'timings': {'retrieval_seconds':0.,'generation_seconds':0.,'verification_seconds':0.,'total_seconds':0.}}


def retrieval_body():
    result = answer_body()
    for k in ('answer','claims','sources','limitations'):
        result.pop(k)
    result.update(status='evidence_found',document_status=None,matches=[])
    result['timings'] = {'retrieval_seconds':0.,'total_seconds':0.,'rerank_seconds':0.}
    return result


def run_fake(scenarios, handler, *, reader=fake_snapshot, mutate=None, profile='full', prepared_profile=None):
    async def exercise():
        cfg = config()
        binding = await prepare(cfg, scenarios, {}, {'cards':[]}, Path.cwd(),
                                profile=prepared_profile or profile, snapshot_reader=fake_snapshot)
        if mutate:
            mutate(binding)
        return await run(cfg, scenarios, {}, {'cards':[]}, Path.cwd(), binding, digest(binding),
                         profile=profile, snapshot_reader=reader, transport=httpx.MockTransport(handler))
    return asyncio.run(exercise())


def test_import_and_cli_import_do_not_load_configuration_or_initialize_dependencies():
    code = '''import sys
from unittest.mock import patch
with patch('socket.socket', side_effect=AssertionError('network forbidden')):
 import src.evaluation.http_e2e
 import src.cli.evaluate_http_e2e
assert not any(k in sys.modules for k in ('dotenv','httpx','sqlalchemy','torch','transformers'))
'''
    result = subprocess.run([sys.executable,'-I','-S','-c',
        'import sys; sys.path.insert(0, '+repr(str(Path.cwd()))+');\n'+code], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.parametrize('url', ['https://example.com','http://example.com','http://localhost',
    'http://user:pass@127.0.0.1','http://127.0.0.1/a','http://127.0.0.1?secret=x',
    'http://127.0.0.1#fragment','http://127.0.0.1:99999','file:///tmp'])
def test_external_and_credential_urls_rejected(url):
    with pytest.raises(EvaluationError):
        base_url(url)


def test_configuration_precedence_does_not_mutate_environment(tmp_path, monkeypatch):
    path = tmp_path/'fictional.env'
    path.write_text('CHAT_BOT_SERVICE_KEY=file-key\nTEST_HRM_DATABASE_URL=postgresql+psycopg://reader:x@localhost/hrm_test\nDATABASE_URL=postgresql+psycopg://reader:x@localhost/corpus_test\nTEST_HRM_DISPOSABLE_CONFIRMED=true\nUNRELATED_PRIVATE_VALUE=not-loaded\n')
    monkeypatch.delenv('UNRELATED_PRIVATE_VALUE', raising=False)
    cfg = configuration(env_file=path,environ={'CHAT_BOT_SERVICE_KEY':'process-key'})
    import os
    assert cfg.service_key == 'process-key' and 'UNRELATED_PRIVATE_VALUE' not in os.environ
    assert 'process-key' not in repr(cfg) and 'reader:x' not in repr(cfg)
    with pytest.raises(EvaluationError):
        configuration(environ={})
    with pytest.raises(EvaluationError, match='separate_hrm'):
        replace(cfg,hrm_url=cfg.corpus_url)


def test_private_outputs_are_exclusive_and_contained(tmp_path):
    with pytest.raises(EvaluationError):
        private_path(tmp_path,'report.json')
    with pytest.raises(EvaluationError):
        private_path(tmp_path,'.tmp/../report.json')
    with pytest.raises(EvaluationError):
        private_path(tmp_path,'.tmp')
    write_private(tmp_path,'.tmp/report.json',{'safe':True})
    with pytest.raises(FileExistsError):
        write_private(tmp_path,'.tmp/report.json',{'safe':False})
    assert json.loads((tmp_path/'.tmp/report.json').read_text()) == {'safe':True}


def test_inventory_covers_each_case_and_endpoint_without_executing(monkeypatch):
    cases = [{'id':'q.topic.001','turns':[{'text':'Fictional'}],'tags':[]},
             {'id':'q.topic.002','turns':[{'text':'One'},{'text':'Two'}],'tags':['multi-turn']},
             {'id':'q.topic.003','turns':[{'text':'Failure'}],'tags':[]}]
    oracles = {(c['id'],0): {'fixture':'current' if c['id'] != 'q.topic.003' else 'missing',
                            'statuses':['answered']} for c in cases}
    items = build_inventory(cases,oracles)
    report = public_inventory(items,{})
    assert len({(s.case_id,s.endpoint) for s in items}) == len(items)
    for case in cases:
        assert sum(s.case_id == case['id'] for s in items) == 2
    assert {s.reason for s in items if s.case_id=='q.topic.002'} == {'conversation_required'}
    assert {s.reason for s in items if s.case_id=='q.topic.003'} == {'controlled_fixture_required'}
    assert 'Fictional' not in json.dumps(report)


def test_bank_profiles_have_distinct_pinned_inventories():
    from src.evaluation.questions import load_dataset

    cases, oracles, inputs = load_dataset(Path.cwd())
    scenarios = build_inventory(cases, oracles)
    full = public_inventory(select_profile(scenarios, 'full'), inputs)
    smoke = public_inventory(select_profile(scenarios, 'smoke'), inputs, profile='smoke')
    assert full['profile'] == 'full' and full['counts'] == {'pending': 4460, 'not_applicable': 216}
    assert full['retrieve_unresolved_default'] == 0
    assert smoke['profile'] == 'smoke' and smoke['counts'] == {'pending': 56}
    assert smoke['selection_fingerprint'] != full['selection_fingerprint']
    assert {(r['case_id'], r['endpoint']) for r in smoke['results']} <= {
        (r['case_id'], r['endpoint']) for r in full['results'] if r['state'] == 'pending'}


def test_independent_selection_and_null_binding():
    data = snapshot()
    older = deepcopy(data['employees'][0])
    older.update(employee_id=41,active=True,updated_at='2026-01-01T00:00:00Z')
    data['employees'][0].update(active=False,job_title=None)
    data['employees'].append(older)
    assert representatives(data['employees'])[7]['active'] is False
    s = scenario(null_fields=['job_title'],statuses=['insufficient_evidence'])
    bindings,roles = build_bindings([s],data)
    assert bindings[s.key]['expected_values'] == {'job_title':None}
    assert roles['inactive'] is None and roles['duplicate_user'] == 7
    assert roles['not_found'] not in {r['user_id'] for r in data['employees']}


def test_representative_checks_all_top_ranked_rows():
    first, = snapshot()['employees']
    conflicting = {**first, 'official_date': '2026-02-01'}
    lower = {**first, 'updated_at': '2025-11-18T00:00:00Z'}
    assert representatives([first, dict(first), conflicting, lower]) == {}
    assert representatives([first, dict(first), lower]) == {7: first}


def test_manager_conflict_requires_the_designated_fixture_and_returns_null():
    scenarios = [s for s in build_inventory([], {}) if s.case_id == 'hrm.role.manager_conflicting']
    data = snapshot()
    row, = data['employees']
    row.update(user_id=910006, manager_id=84, manager_code='WRONG')
    manager = {**row, 'user_id': None, 'employee_id': 84, 'employee_code': 'CORRECT',
               'manager_id': None, 'manager_code': None}
    data['employees'].append(manager)
    bindings, roles = build_bindings(scenarios, data)
    assert roles['manager_conflicting'] == 910006
    for s in scenarios:
        binding = bindings[s.key]
        assert binding['state'] == 'ready' and binding['expected_values'] == {'manager_name': None}
        is_answer = s.endpoint.endswith('/answer')
        payload = answer_body() if is_answer else retrieval_body()
        payload['evidence'][0].update(field='manager_name', subject='manager', label='Quản lý trực tiếp', value=None)
        payload['application']['state'] = 'partial'
        payload['status'] = 'insufficient_evidence' if is_answer else 'no_evidence'
        if is_answer:
            payload['claims'][0]['text'] = 'Hồ sơ hiện chưa có thông tin về quản lý trực tiếp.'
            payload['answer'] = payload['claims'][0]['text'] + ' [C1]'
        assert check_response(s, binding, 200, payload, {'cache-control': 'no-store'}, {'cards': []}, None) == []
    row['manager_code'] = ' NULL '
    bindings, roles = build_bindings(scenarios, data)
    assert roles['manager'] == 910006 and roles['manager_conflicting'] is None
    assert {b['reason'] for b in bindings.values()} == {'profile_role_missing'}


def test_missing_profile_or_source_is_blocked_before_response():
    s = scenario(employee_fields=['probation_date'],required_source_groups=[['missing']])
    bindings,_ = build_bindings([s],snapshot())
    assert bindings[s.key]['state']=='blocked' and bindings[s.key]['reason']=='policy_source_missing'


def test_two_endpoint_expectations_are_independent():
    a,r = scenario(),scenario('/api/v1/retrieve')
    b = {'expected_values':{'job_title':'Analyst'}}
    assert check_response(a,b,200,answer_body(),{'cache-control':'no-store'},{'cards':[]},'2026-11-18')==[]
    assert check_response(r,b,200,retrieval_body(),{'cache-control':'no-store'},{'cards':[]},'2026-11-18')==[]
    assert 'response_schema' in check_response(r,b,200,answer_body(),{}, {'cards':[]},'2026-11-18')
    undisplayed = answer_body() | {'answer':'Fictional limitation','claims':[],'limitations':['Fictional limitation']}
    assert 'employee_claim_evidence' in check_response(a,b,200,undisplayed,
        {'cache-control':'no-store'},{'cards':[]},'2026-11-18')


@pytest.mark.parametrize(('field', 'value', 'label', 'claim'), [
    ('job_title', 'Analyst', 'Chức danh', 'Chức danh của bạn: Analyst.'),
    ('job_title', None, 'Chức danh', 'Hồ sơ hiện chưa có thông tin về chức danh.'),
    ('official_date', '2026-11-19', 'Ngày chính thức', 'Ngày chính thức của bạn: 19/11/2026.'),
])
def test_employee_claim_must_display_the_linked_value(field, value, label, claim):
    s = scenario(employee_fields=[field], expected_employee_claim_fields=[field])
    body = answer_body(value)
    body['evidence'][0].update(field=field, label=label)
    body['claims'][0]['text'] = claim
    body['answer'] = claim + ' [C1]'
    args = ({'cache-control': 'no-store'}, {'cards': []}, None)
    binding = {'expected_values': {field: value}}
    assert check_response(s, binding, 200, body, *args) == []
    body['claims'][0]['text'] = f'{label} của bạn: Director.'
    body['answer'] = body['claims'][0]['text'] + ' [C1]'
    assert check_response(s, binding, 200, body, *args) == ['employee_claim_value']


@pytest.mark.parametrize('mutation,expected', [
    (lambda b:b.update(schema_version=1),'response_schema'),
    (lambda b:b['evidence'][0].update(value='Wrong value'),'employee_values'),
    (lambda b:b['evidence'][0].update(field='department_name'),'employee_fields'),
    (lambda b:b['evidence'][0].update(subject='manager'),'employee_subject'),
    (lambda b:b['claims'][0].update(evidence_refs=['missing']),'response_schema'),
    (lambda b:b['evidence'][0].update(field='account_id'),'response_schema'),
])
def test_response_errors_fail_without_echoing_values(mutation,expected):
    body=answer_body();mutation(body)
    failures=check_response(scenario(),{'expected_values':{'job_title':'Analyst'}},200,body,
        {'cache-control':'no-store'},{'cards':[]},'2026-11-18')
    assert expected in failures and 'Wrong value' not in json.dumps(failures)


@pytest.mark.parametrize('profile', ['full', 'smoke'])
def test_health_order_and_sequential_stateless_payloads(profile):
    calls=[]
    def handler(request):
        calls.append(request)
        if request.method=='GET':
            return httpx.Response(200,json={'status':'alive' if request.url.path.endswith('/live') else 'ready'})
        return httpx.Response(200,json=retrieval_body() if request.url.path.endswith('/retrieve') else answer_body(),headers={'Cache-Control':'no-store'})
    result=run_fake([scenario('/api/v1/retrieve'),scenario()],handler,profile=profile)
    assert [r.url.path for r in calls]==['/health/live','/health/ready','/health/answer','/api/v1/retrieve','/api/v1/answer']
    assert result['counts']=={'passed':2} and result['exit_code']==0 and result['profile']==profile
    assert 'X-Rita-Service-Key' not in calls[0].headers
    for req in calls[3:]:
        assert set(json.loads(req.content))=={'text','hrm_user_id','top_k'}
    assert 'Analyst' not in json.dumps(result) and config().service_key not in json.dumps(result)


@pytest.mark.parametrize('status', [401,503,302])
def test_health_failure_or_redirect_stops_all_business_calls(status):
    calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(status,json={'status':'ready'},headers={'Location':'http://example.invalid'})
    result=run_fake([scenario()],handler)
    assert len(calls)==1 and result['exit_code']==2 and result['counts']=={'blocked':1}


def test_snapshot_drift_never_counts_as_passed():
    calls=[];snapshots=[]
    async def reader(*args):
        value=snapshot();snapshots.append(value)
        if len(snapshots)==2:value['fingerprint']='changed'
        return value
    def handler(req):
        calls.append(req)
        return httpx.Response(200,json={'status':'alive' if req.url.path.endswith('/live') else 'ready'} if req.method=='GET' else answer_body(),headers={'Cache-Control':'no-store'})
    result=run_fake([scenario()],handler,reader=reader)
    assert result['problem']=='snapshot_drift_after_run' and result['counts']=={'blocked':1}


def test_binding_coverage_precedes_any_http():
    def handler(req):
        pytest.fail('HTTP must not run for invalid binding')
    result=run_fake([scenario()],handler,mutate=lambda b:b['bindings'].clear())
    assert result['problem']=='binding_coverage' and result['exit_code']==2


def test_profile_and_selection_are_checked_before_any_http():
    def handler(req):
        pytest.fail('HTTP must not run for a different selection')

    result = run_fake([scenario()], handler, profile='smoke', prepared_profile='full')
    assert result['profile'] == 'smoke' and result['problem'] == 'binding_profile_mismatch'
    result = run_fake([scenario()], handler, mutate=lambda b: b.update(selection_fingerprint='changed'))
    assert result['problem'] == 'binding_selection_mismatch' and result['exit_code'] == 2


def test_transport_timeout_not_retried_and_remaining_cases_blocked():
    calls=[]
    def handler(req):
        calls.append(req)
        if req.method=='GET':return httpx.Response(200,json={'status':'alive' if req.url.path.endswith('/live') else 'ready'})
        raise httpx.ReadTimeout('PRIVATE_DRIVER_MESSAGE',request=req)
    result=run_fake([scenario(),replace(scenario(),case_id='q.employee.002')],handler)
    assert len(calls)==4 and result['counts']=={'failed':1,'blocked':1}
    assert 'PRIVATE_DRIVER_MESSAGE' not in json.dumps(result)


def test_protocol_payloads_keep_identity_and_auth_changes_explicit():
    for name,expected in [('missing_identity',None),('boolean_identity',True),('top_k_boolean',True)]:
        s=replace(scenario(),protocol=name)
        body,headers=request_for(s,{'hrm_user_id':7},'fictional-key')
        assert headers=={'X-Rita-Service-Key':'fictional-key'}
        if name=='missing_identity':assert 'hrm_user_id' not in body
        else:assert body['top_k' if name=='top_k_boolean' else 'hrm_user_id'] is expected


def test_all_protocol_cases_use_independent_expected_errors():
    items=[s for s in build_inventory([],{}) if s.protocol]
    def handler(req):
        if req.method=='GET':return httpx.Response(200,json={'status':'alive' if req.url.path.endswith('/live') else 'ready'})
        body=json.loads(req.content)
        auth=req.headers.get('X-Rita-Service-Key')==config().service_key
        status=422 if auth else 401
        return httpx.Response(status,json={'error':{'code':'invalid_request' if auth else 'service_authentication_required'}},headers={'Cache-Control':'no-store'})
    result=run_fake(items,handler)
    assert result['counts']=={'passed':22} and result['exit_code']==0


def test_preflight_drift_blocks_before_business_requests():
    calls=[]
    async def drift(*args):
        result=snapshot();result['fingerprint']='changed';return result
    def handler(req):
        calls.append(req)
        return httpx.Response(200,json={'status':'alive' if req.url.path.endswith('/live') else 'ready'})
    result=run_fake([scenario()],handler,reader=drift)
    assert len(calls)==3 and result['problem']=='snapshot_drift_before_run'


def test_missing_role_does_not_become_pass_or_trigger_request():
    s=scenario(employee_fields=['probation_date'])
    calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(200,json={'status':'alive' if req.url.path.endswith('/live') else 'ready'})
    result=run_fake([s],handler)
    assert len(calls)==3 and result['exit_code']==2 and result['counts']=={'blocked':1}


@pytest.mark.parametrize('error', [RuntimeError('PRIVATE_CONNECTION_STRING'), EvaluationError('employee_survey_too_large')])
def test_missing_final_snapshot_invalidates_previous_pass(error):
    n=0
    async def reader(*args):
        nonlocal n
        n+=1
        if n==2:raise error
        return snapshot()
    def handler(req):
        return httpx.Response(200,json={'status':'alive' if req.url.path.endswith('/live') else 'ready'} if req.method=='GET' else answer_body(),headers={'Cache-Control':'no-store'})
    result=run_fake([scenario()],handler,reader=reader)
    assert result['counts']=={'blocked':1} and result['exit_code']==2
    assert result['problem']=='final_snapshot_unavailable' and 'PRIVATE_CONNECTION_STRING' not in json.dumps(result)


def test_calculation_expected_from_pinned_profile_and_explicit_date():
    from src.evaluation.http_e2e.assertions import calculation_expectation
    s=scenario(employee_fields=['official_date'])
    s.text='Ngày chính thức của tôi đến 2026-02-28 bao nhiêu tháng?'
    binding={'expected_values':{'official_date':'2026-01-31'}}
    expected=calculation_expectation(s,binding,{'cards':[]},'2026-11-18')
    assert expected=={'operation':'completed_months','value':'1','unit':'month','as_of':'2026-02-28','field':'official_date','policy_rule':False}
    body=answer_body()
    body['evidence'][0].update(field='official_date',value='2026-01-31')
    body['evidence'].append({'kind':'user_input','id':'U1','slot':'as_of','label':'Date',
        'value':'2026-02-28','hypothetical':False})
    body['evidence'].append({'kind':'calculation','id':'K1','operation':'completed_months','label':'Fictional months',
        'value':'1','unit':'month','input_refs':['F1','U1'],'rule_refs':[],'as_of':'2026-02-28'})
    body['claims'].append({'id':'C2','text':'1 month','evidence_refs':['K1']})
    body['answer']+='\n\n1 month [C2]'
    assert check_response(s,binding,200,body,{'cache-control':'no-store'},{'cards':[]},'2026-11-18')==[]
    body['evidence'][-1]['value']='2'
    assert 'calculation_value' in check_response(s,binding,200,body,{'cache-control':'no-store'},{'cards':[]},'2026-11-18')
    r=replace(s,endpoint='/api/v1/retrieve')
    retrieve=retrieval_body();retrieve['evidence']=body['evidence']
    assert 'retrieve_does_not_answer' in check_response(r,binding,200,retrieve,{'cache-control':'no-store'},{'cards':[]},'2026-11-18')


def test_readonly_snapshot_adapters_use_no_real_resources(tmp_path,monkeypatch):
    from contextlib import asynccontextmanager
    import hashlib
    import sqlalchemy.ext.asyncio
    from src.evaluation.http_e2e.snapshot import read_snapshot,reference_key
    from src.type_b.employee_store import service as employee_module
    from src.type_b.employee_store import connection as connection_module
    closed=[];statements=[]
    content='Fictional policy evidence'
    ref={'document_id':'doc','revision_id':'rev','chunk_index':0,'content_sha256':hashlib.sha256(content.encode()).hexdigest(),
         'source_sha256':'a'*64,'quote':content,'metadata':{'title':'Fictional policy','applicability':None,
         'effective_from':None,'effective_to':None,'version_label':None,'source_path':'fictional.pdf'}}
    catalog={'cards':[{'id':'task.test','sections':{'overview':{'units':[{'refs':[ref]}]}}}]}
    s=scenario(employee_fields=[],task_ids_any=['task.test'])
    root=tmp_path/'repo';(root/'artifacts').mkdir(parents=True)
    (root/'artifacts/runtime-assets.json').write_text(json.dumps({'models':{'e5':{'revision':'a'*40,'files_sha256':'b'*64}}}))
    class Result:
        def __init__(self,rows):self.rows=rows
        def mappings(self):return self.rows
    class Connection:
        async def execute(self,sql,params=None):
            value=str(sql);statements.append(value)
            if 'employee_sso_store' in value:return Result(snapshot()['employees'])
            if value.startswith('SELECT r.document_id'):
                return Result([{'document_id':'doc','revision_id':'rev','chunk_index':0,'content':content,'sha256':'a'*64,
                    'chunk_id':'chunk','run_id':'run',**ref['metadata']}])
            return None
        async def scalar(self,sql):return 'on'
        @asynccontextmanager
        async def begin(self):yield
    class Source:
        def __init__(self,*args):pass
        @asynccontextmanager
        async def _read_scope(self):yield Connection()
        async def close(self):closed.append('source')
    class Employee:
        def __init__(self,*args):pass
        async def check_source(self):return {'readonly':True}
        async def close(self):closed.append('employee')
    class Engine:
        @asynccontextmanager
        async def connect(self):yield Connection()
        async def dispose(self):closed.append('corpus')
    monkeypatch.setattr(employee_module,'EmployeeSourceService',Employee)
    # read_snapshot imports the re-export as well.
    import src.type_b.employee_store as employee_package
    monkeypatch.setattr(employee_package,'EmployeeSourceService',Employee)
    monkeypatch.setattr(connection_module,'ReadOnlySource',Source)
    monkeypatch.setattr(sqlalchemy.ext.asyncio,'create_async_engine',lambda *a,**k:Engine())
    result=asyncio.run(read_snapshot(config(),[s],catalog,root))
    assert result['valid_references']==[reference_key(ref)] and result['documents']==['doc']
    assert set(closed)=={'source','employee','corpus'}
    assert all(sql.startswith(('SELECT','SET TRANSACTION READ ONLY')) for sql in statements)
    projection=next(sql for sql in statements if 'employee_sso_store' in sql)
    assert 'bank_account' not in projection and 'families' not in projection and 'LIMIT 10001' in projection


def test_cli_inventory_never_reads_dotenv_or_network(tmp_path,monkeypatch,capsys):
    from src.cli.evaluate_http_e2e import main
    from src.evaluation.http_e2e import inventory as module
    import src.paths
    monkeypatch.setattr(src.paths,'REPOSITORY_ROOT',tmp_path)
    monkeypatch.setattr(module,'load_inventory',lambda root:([scenario()],{}, {'cards':[]}))
    monkeypatch.setattr('socket.create_connection',lambda *a,**k:pytest.fail('Network forbidden'))
    assert main(['inventory','--output','.tmp/local-inventory.json'])==0
    assert json.loads((tmp_path/'.tmp/local-inventory.json').read_text())['counts']=={'pending':1}
    assert main(['inventory','--output','.tmp/local-inventory.json'])==2
    assert main(['inventory','--output','.tmp/other.json','--env-file','PRIVATE_DO_NOT_READ.env'])==2
    assert not (tmp_path/'.tmp/other.json').exists()
    assert 'PRIVATE_DO_NOT_READ' not in capsys.readouterr().out


def test_document_citations_and_retrieval_content_match_pinned_sql_snapshot():
    import hashlib
    text='Fictional policy claim'
    metadata=dict(title='Fictional policy',applicability=None,effective_from=None,effective_to=None,version_label=None,source_path='fictional.pdf')
    ref=dict(document_id='doc',revision_id='rev',chunk_index=0,quote=text,metadata=metadata)
    catalog={'cards':[{'id':'task.policy','sections':{'overview':{'units':[{'refs':[ref]}]}}}]}
    pinned={'chunk_id':'chunk','document_id':'doc','revision_id':'rev','run_id':'run','chunk_index':0,
            **metadata,'source_spans':[],'author_id':None,'role':None,'content_hash':hashlib.sha256(text.encode()).hexdigest()}
    origin={k:pinned[k] for k in ('chunk_id','document_id','revision_id','run_id','source_path','source_spans')}
    s=scenario(employee_fields=[],task_ids_any=['task.policy'],required_source_groups=[['doc']],claims='required',statuses=['partial'])
    b={'expected_values':{}}
    body=answer_body();body.update(status='partial',answer=text+' [C1]',evidence=[{'kind':'document','id':'D1','source_id':'S1','quote':text}],
        claims=[{'id':'C1','text':text,'evidence_refs':['D1']}],sources=[{'source_id':'S1',**{k:v for k,v in metadata.items() if k!='source_path'},'origins':[deepcopy(origin)]}])
    assert check_response(s,b,200,body,{'cache-control':'no-store'},catalog,'2026-11-18',corpus_records={'chunk':pinned})==[]
    body['sources'][0]['origins'][0]['run_id']='wrong-run'
    assert 'answer_snapshot_provenance' in check_response(s,b,200,body,{'cache-control':'no-store'},catalog,'2026-11-18',corpus_records={'chunk':pinned})
    body['sources'][0]['origins'][0]=deepcopy(origin)
    body['evidence'][0]['quote']='Fabricated quote'
    assert 'citation_provenance' in check_response(s,b,200,body,{'cache-control':'no-store'},catalog,'2026-11-18',corpus_records={'chunk':pinned})
    r=replace(s,endpoint='/api/v1/retrieve')
    data=retrieval_body();data.update(document_status='matches_found',evidence=[],matches=[{k:v for k,v in pinned.items() if k!='content_hash'} | {'content':text,'cosine_distance':0.1,'rerank_score':None}])
    data['relevance']={'status':'unassessed','warnings':['evidence_completeness_not_assessed']}
    assert check_response(r,b,200,data,{'cache-control':'no-store'},catalog,'2026-11-18',corpus_records={'chunk':pinned})==[]
    data['matches'][0]['content']='Wrong source content'
    assert 'retrieval_snapshot_provenance' in check_response(r,b,200,data,{'cache-control':'no-store'},catalog,'2026-11-18',corpus_records={'chunk':pinned})


@pytest.mark.parametrize(('task_id', 'scope'), [
    ('task.policy', 'Fictional scope'), ('task.leave.workit.unpaid', 'Workit'),
])
def test_answer_checks_display_claim_limitations_and_explicit_aspect(task_id, scope):
    ref = {'document_id':'doc', 'revision_id':'rev', 'chunk_index':1, 'quote':'Fictional proof', 'metadata':{}}
    other = ref | {'chunk_index':0, 'quote':'Different aspect'}
    unit = {'text':'Fictional condition', 'refs':[ref]}
    catalog = {'cards':[{'id':task_id, 'label':'Fictional scope', 'sections':{'conditions':{'units':[unit]},
        'overview':{'units':[unit | {'refs':[other]}]}}}]}
    s = scenario(employee_fields=[], claims='required', task_ids_any=[task_id],
        expected_aspects=['conditions'], expected_policy_claim_texts=['Fictional condition'],
        required_limitations=['Fictional limit'])
    s.tags = ('aspect:overview',)  # The explicit oracle wins.
    body = answer_body()
    body.update(answer='Fictional condition [C1]', claims=[dict(id='C1', text='Fictional condition', evidence_refs=['D1'])],
        limitations=['  Fictional   limit  '], evidence=[dict(kind='document', id='D1', source_id='S1', quote=ref['quote'])],
        sources=[dict(source_id='S1', title=None, applicability=None, effective_from=None, effective_to=None,
                      version_label=None, origins=[dict(document_id='doc', revision_id='rev', chunk_index=1)])])
    def failures(payload):
        return check_response(s, {'expected_values':{}}, 200, payload, {'cache-control':'no-store'}, catalog, '2026-11-19')
    assert failures(body) == []
    prefix = f'Phạm vi trả lời là {scope}.\n\n'
    assert failures(body | {'answer':prefix + body['answer']}) == []
    assert 'answer_display' in failures(body | {'answer':prefix + prefix + body['answer']})
    assert 'answer_display' in failures(body | {'answer':'Phạm vi trả lời là Other.\n' + body['answer']})
    assert 'answer_display' in failures(body | {'answer':'Unrelated conclusion'})
    assert 'required_limitations' in failures(body | {'limitations':[]})
    claim = body['claims'][0] | {'text':'Different condition'}
    assert 'policy_claim_texts' in failures(body | {'claims':[claim], 'answer':'Different condition [C1]'})
    assert 'policy_claim_texts' in failures(body | {'claims':body['claims'] + [body['claims'][0] | {'id':'C2'}]})
    body['evidence'][0]['quote'] = other['quote']
    body['sources'][0]['origins'][0]['chunk_index'] = 0
    assert 'required_task_evidence' in failures(body)


def test_authored_excerpt_checks_snapshot_even_without_template():
    import hashlib
    text = 'Fictional excerpt'
    limit = 'Fictional unassessed limitation'
    s = scenario(employee_fields=[], statuses=['partial'], claims='required', answer_kind='excerpt',
                 required_source_groups=[['doc']], required_limitations=[limit])
    origin = dict(chunk_id='chunk', revision_id='rev', document_id='doc', run_id='run', source_path='f.pdf', source_spans=[])
    body = answer_body() | dict(status='partial', answer=text+' [C1]', fallback_reason='document_lookup',
        claims=[dict(id='C1', text=text, evidence_refs=['D1'])], limitations=[limit],
        evidence=[dict(kind='document', id='D1', source_id='S1', quote=text)],
        sources=[dict(source_id='S1', title=None, applicability=None, effective_from=None,
                      effective_to=None, version_label=None, origins=[origin])])
    pinned = origin | {'content_hash':hashlib.sha256(text.encode()).hexdigest()}
    def failures(payload):
        return check_response(s, {'expected_values':{}}, 200, payload, {'cache-control':'no-store'},
                              {'cards':[]}, '2026-11-19', corpus_records={'chunk':pinned})
    assert failures(body) == []
    assert 'answer_kind' in failures(body | {'fallback_reason':None})
    assert 'required_limitations' in failures(body | {'limitations':[]})
    body['evidence'][0]['quote'] = 'Altered excerpt'
    assert {'excerpt_snapshot_content','excerpt_claim_text'} <= set(failures(body))


def test_preflight_partial_requires_a_complete_claim_and_not_a_complete_section():
    def ref(n):
        return dict(document_id='doc', revision_id='rev', chunk_index=n, content_sha256=str(n)*64)
    a,b,c = [ref(n) for n in (1,2,3)]
    catalog = {'cards':[{'id':'task.test', 'sections':{'overview':{'units':[
        dict(text='First claim',refs=[a,b]), dict(text='Second claim',refs=[c])]}}}]}
    s = scenario(employee_fields=[], task_ids_any=['task.test'], claims='required', statuses=['partial'],
                 required_source_groups=[['doc']])
    r = replace(s,endpoint='/api/v1/retrieve')
    full = replace(s,case_id='q.complete',oracle=s.oracle | {'statuses':['answered']})
    exact = replace(s,case_id='q.exact',oracle=s.oracle | {'expected_policy_claim_texts':['First claim']})
    scenarios = [s,r,full,exact]
    _, requirements = reference_inventory(scenarios,catalog)
    data = snapshot() | {'task_requirements':requirements,'documents':['doc'],'valid_references':[reference_key(c)]}
    bindings,_ = build_bindings(scenarios,data)
    assert [bindings[x.key]['state'] for x in scenarios] == ['ready','ready','blocked','blocked']
    data['valid_references']=[reference_key(a)]
    bindings,_ = build_bindings(scenarios,data)
    assert bindings[s.key]['state']=='blocked' and bindings[r.key]['state']=='ready'


def test_retrieve_explicit_denial_rejects_documents_and_employee_evidence():
    s = scenario('/api/v1/retrieve', employee_fields=[], retrieve_statuses=['unsupported'], retrieve_documents='none')
    body = retrieval_body() | {'status':'unsupported','evidence':[]}
    def failures(payload):
        return check_response(s, {'expected_values':{}}, 200, payload, {'cache-control':'no-store'},
                              {'cards':[]}, '2026-11-19')
    assert failures(body) == []
    assert 'status' in failures(body | {'status':'evidence_found'})
    assert 'retrieve_document_lookup' in failures(body | {'document_status':'no_matches'})
    assert 'employee_fields' in failures(body | {'evidence':retrieval_body()['evidence']})
