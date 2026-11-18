"""Contextual composition/provenance boundaries with fictional, immutable personnel."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timezone
from types import MappingProxyType

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from src.answering.contextual import prepare, requirements, compose
from src.answering.context_spec import ContextSpec
from src.answering.context_contracts import EvidenceEnvelope
from src.answering.calculations import calculate
from src.type_b.employee_store.contracts import EmployeeContext
from src.type_b.config import TypeBSettings
from src.api.app import create_app
from src.tests.http_fixtures import SERVICE_HEADERS
from src.tests import test_answering as fixtures

sample = fixtures.sample


def snapshot(**values):
    return EmployeeContext(MappingProxyType(values), None, '2026-11-18T01:00:00Z', None)


def test_source_projection_is_only_displayed_or_calculated_fields(sample):
    spec = ContextSpec(mode='branch', groups=['work', 'employment'],
        facts=['official_date'], template='Thông tin người hỏi: {employee_name}.',
        prompts={'as_of':'Ngày xét là ngày nào?'}, note='Fictional reviewed requirements')
    card = sample.catalog.cards[0].model_copy(update={'context':spec})
    catalog = sample.catalog.model_copy(update={'cards':[card]})
    plan = prepare('Sinh con của tôi được bao nhiêu?', catalog, sample.lexicon)
    assert requirements(plan) == (('employee_name','official_date'), False)
    assert prepare('Gia đình của tôi sinh con?', catalog, sample.lexicon).hypothetical is False
    evidence, claims = [], []
    application, limits = compose(plan, snapshot(employee_name='Synthetic',official_date=None), 'applied', evidence, claims)
    assert application.state == 'partial' and application.mode == 'conditional'
    assert application.missing_inputs[0].slot == 'as_of'
    assert {e['field'] for e in evidence} == {'employee_name','official_date'}
    assert any('chưa có dữ liệu' in c['text'] for c in claims)
    assert limits


def test_one_http_answer_contains_policy_and_employee_citations(sample):
    spec = ContextSpec(mode='fill',groups=['work'],template='Người hỏi: {employee_name}; chức danh: {job_title}.',note='Fictional')
    sample.catalog = sample.catalog.model_copy(update={'cards':[sample.catalog.cards[0].model_copy(update={'context':spec})]})
    owner = fixtures.service(sample)
    owner.context.settings = TypeBSettings(True)
    owner.context.available = True
    employee = SimpleNamespace(read_context=AsyncMock(return_value=snapshot(employee_name='Synthetic', job_title='Analyst')), close=AsyncMock())
    owner.context.employee = employee
    with TestClient(create_app(owner.settings,service_factory=lambda _:owner),headers=SERVICE_HEADERS) as client:
        data=client.post('/api/v1/answer',json={'text':'Sinh con được bao nhiêu?','hrm_user_id':7}).json()
    assert data['status']=='answered' and data['schema_version']==2
    assert {e['kind'] for e in data['evidence']}=={'employee','document'}
    assert 'Synthetic' in data['answer'] and '100 credits' in data['answer']
    employee.read_context.assert_awaited_once_with(7,('employee_name','job_title'),manager=False)
    assert all(c['evidence_refs'] for c in data['claims'])


def test_explicit_dates_calculated_but_retrieve_never_calculates():
    plan=prepare('Ngày chính thức của tôi đến 2026-02-28 bao nhiêu tháng?')
    evidence, claims=[],[]
    application,_=compose(plan,snapshot(official_date='2026-01-31'),'applied',evidence,claims)
    calc=next(e for e in evidence if e['kind']=='calculation')
    assert (calc['value'],calc['unit'],calc['as_of'])==('1','month','2026-02-28')
    EvidenceEnvelope(evidence=evidence,application=application)
    evidence,claims=[],[]
    compose(plan,snapshot(official_date='2026-01-31'),'applied',evidence,claims,calculate_values=False)
    assert all(e['kind']!='calculation' for e in evidence)


@pytest.mark.parametrize('operation,values,as_of,result',[
    ('interval_days',['2024-02-28'],'2024-03-01',('2','day')),
    ('age_years',['2010-06-02'],'2025-06-01',('14','year')),
    ('multiply',['0.1','3'],None,('0.3','number')),
    ('add',['1.2','2.3'],None,('3.5','number')),
])
def test_exact_calendar_and_decimal_math(operation,values,as_of,result):
    assert calculate(operation,values,as_of=as_of)==result


@pytest.mark.parametrize('mutation',['missing','cycle','private_field','duplicate'])
def test_evidence_graph_rejects_invalid_or_private_nodes(mutation):
    fact=dict(kind='employee',id='F1',field='job_title',subject='self',label='Role',value=None,observed_at='2026-01-01T00:00:00Z',updated_at=None)
    calc=dict(kind='calculation',id='K1',operation='count',label='Count',value='1',unit='number',input_refs=['F1'],rule_refs=[],as_of=None)
    if mutation=='missing':calc['input_refs']=['missing']
    if mutation=='cycle':calc['input_refs']=['K1']
    if mutation=='private_field':fact['field']='notes'
    nodes=[fact,calc] if mutation!='duplicate' else [fact,fact]
    with pytest.raises(ValidationError):EvidenceEnvelope(evidence=nodes)


def test_user_inputs_remain_assertions_and_other_subject_never_reads_self(sample):
    spec=ContextSpec(mode='branch',groups=['work'],template='{employee_name}',prompts={'as_of':'Ngày xét?'},note='Fictional')
    sample.catalog=sample.catalog.model_copy(update={'cards':[sample.catalog.cards[0].model_copy(update={'context':spec})]})
    plan=prepare('Giả sử sinh con của tôi; Ngày xét: 2026-11-18',sample.catalog,sample.lexicon)
    nodes,claims=[],[]
    app,_=compose(plan,snapshot(employee_name='Synthetic'),'applied',nodes,claims)
    assert app.mode=='hypothetical' and app.state=='partial'
    assert next(e for e in nodes if e['kind']=='user_input')['hypothetical']
    other=prepare('Chức danh của đồng nghiệp Lan?',sample.catalog,sample.lexicon)
    assert other.unsupported_person and requirements(other)==((),False)


def test_all_current_cards_have_explicit_context_disposition():
    import json
    from src.paths import REPOSITORY_ROOT
    from src.answering.catalog import Catalog
    data=Catalog.model_validate(json.loads((REPOSITORY_ROOT/'artifacts/answers/catalog.json').read_text(encoding='utf8')))
    assert data.schema_version==3
    assert all(card.context is not None and card.context.note for card in data.cards)


def test_conflicting_user_assertion_is_not_downgraded_to_missing_input():
    spec=ContextSpec(mode='branch',groups=['employment'],facts=['official_date'],prompts={'as_of':'Ngày xét?'},note='Fictional')
    plan=replace(prepare('Tôi đã chính thức, ngày chính thức của tôi đến nay có thâm niên bao lâu?'),spec=spec)
    nodes,claims=[],[]
    app,limitations=compose(plan,snapshot(official_date=None),'applied',nodes,claims)
    assert app.state=='conflict' and app.conflicts and app.missing_inputs
    assert next(e for e in nodes if e['kind']=='employee')['value'] is None
