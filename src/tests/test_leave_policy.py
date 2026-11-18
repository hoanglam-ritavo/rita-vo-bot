"""Bounded Vietnamese recognition and source-bound ordering with fictional evidence."""
from src.tests.http_fixtures import SERVICE_KEY, SERVICE_HEADERS, http_service_environment
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi.testclient import TestClient

from src.answering.catalog import Catalog as AnswerCatalog, load_catalog
from src.answering.pipeline import evaluate_question, render, select_cards
from src.artifact_authoring import sources, workflow, impact
from src.api.app import create_app
from src.api.config import Settings
from src.api.service import ApiService
from src.paths import REPOSITORY_ROOT
from src.retrieval.policy import RetrievalPolicy, Binding, validate_catalog
from src.retrieval import RetrievalService, storage
from src.retrieval.reranking import RerankSettings
from src.retrieval.semantics import expansion_candidates
from src.tests.test_artifact_authoring import authored


@pytest.fixture
def policy():
    p = RetrievalPolicy()
    p.start()
    assert p.catalog is not None
    return p


@pytest.mark.parametrize('question,family,subtype,system,days,sessions,granularity', [
    ('làm sao để đăng ký nghỉ 2 hôm.', 'leave',None,None,2,None,'day'),
    ('Xin nghỉ hai ngày trên Workit', 'leave',None,'workit',2,None,'day'),
    ('Dang ky nghi phep 2 ngay tren Workit?', 'leave','annual','workit',2,None,'day'),
    ('Xin nghỉ phép không lương hai ngày Workit', 'leave','unpaid','workit',2,None,'day'),
    ('Nghỉ phép năm ngày trên E-Leave', 'leave','annual','eleave',5,None,'day'),
    ('Nghỉ phép nửa ngày trên ELeave', 'leave','annual','eleave',.5,None,'session'),
    ('Nghỉ phép 2 buổi trên E-Leave', 'leave','annual','eleave',None,2,'session'),
    ('Xin nghỉ phép chiều thứ 6 và sáng thứ 7 E-Leave', 'leave','annual','eleave',None,None,'session'),
    ('Xin nghỉ phép ngày 2/12 trên Workit', 'leave','annual','workit',None,None,'day'),
    ('Báo trước khi xin nghỉ 2 ngày Workit', 'leave',None,'workit',2,None,'day'),
    ('Xin thôi việc', 'resignation',None,None,None,None,'day'),
    ('Đăng ký đi trễ Workit', 'attendance',None,'workit',None,None,'day'),
    ('Tìm tài liệu mạng máy tính', None,None,None,None,None,'day'),
])
def test_recognition_keeps_duration_distinct_from_calendar_dates(policy, question, family, subtype, system, days, sessions, granularity):
    q = policy.analyze(question)
    assert (q.family,q.subtype,q.system,q.days,q.sessions,q.granularity) == (family,subtype,system,days,sessions,granularity)
    assert not q.ambiguous


@pytest.mark.parametrize('question', [
    'Xin nghỉ việc 2 hôm', 'Nghỉ việc 2 hôm', 'Nghỉ việc', 'Xin nghỉ và đi trễ Workit', 'Xin nghỉ phép hoặc nghỉ thai sản Workit',
    'Không muốn đăng ký nghỉ phép Workit', 'Xin nghỉ hơn 2 ngày Workit', 'Xin nghỉ 1-3 ngày Workit',
    'Xin nghỉ 2 ngày và 3 ngày', 'Xin nghỉ 0 ngày', 'Xin nghỉ phép trên Workit và E-Leave',
    'Tôi có được nghỉ phép hai ngày không?',
])
def test_ambiguity_never_silently_selects_a_business_branch(policy, question):
    assert policy.analyze(question).ambiguous
    assert policy.resolve(question).state == 'clarify'


def test_clarification_is_specific_and_retains_two_days(policy):
    r = policy.resolve('làm sao để đăng ký nghỉ 2 hôm.')
    assert r.state == 'clarify'
    assert r.message == ('Bạn muốn đăng ký nghỉ phép hay nghỉ không lương trong 2 ngày? '
                         'Bạn đang dùng Workit hay E-Leave để gửi đơn?')
    r = policy.resolve('Xin nghỉ phép 2 ngày')
    assert 'Workit hay E-Leave' in r.message and '2 ngày' in r.message
    assert 'hay nghỉ không lương' not in r.message


@pytest.mark.parametrize('question,task', [
    ('Cách đăng ký nghỉ phép hai ngày Workit', 'task.leave.workit.annual'),
    ('Xin nghỉ không lương 2 ngày Workit', 'task.leave.workit.unpaid'),
    ('Cách đăng ký nghỉ phép 2 ngày E-Leave', 'task.leave.eleave.annual'),
    ('Xin nghỉ phép buổi sáng thứ 2 E-Leave', 'task.leave.eleave.sessions'),
    ('Xin nghỉ phép 2 ngày phải báo trước bao lâu?', 'task.leave.policy.annual'),
    ('Điều kiện nghỉ không lương?', 'task.leave.policy.unpaid'),
])
def test_clear_questions_route_to_source_bound_tasks(policy, question, task):
    assert policy.resolve(question).task_id == task


def test_eleave_does_not_invent_unlisted_leave_types(policy):
    assert policy.resolve('Đăng ký nghỉ không lương E-Leave').state == 'unsupported'
    assert policy.resolve('Hủy đơn nghỉ phép Workit').state == 'unsupported'


def test_explicit_task_alias_does_not_hide_conflicting_leave_type(policy):
    assert policy.resolve('Đăng ký nghỉ phép Workit và nghỉ thai sản').state == 'clarify'


def test_conflicting_inpatient_sources_require_an_explicit_source(policy):
    question='Hỗ trợ nằm viện của công ty'
    resolution=policy.resolve(question)
    assert resolution.state=='clarify' and len(resolution.candidates)==2
    assert policy.references(question)==[]
    benefits='d1ed5f3f-57ed-465d-a080-97c4a39efd8d'
    labor='7f1f2cf6-e181-42a8-8caf-f9429094e661'
    assert policy.resolve(question+' nguồn '+benefits).task_id=='task.business.policy.inpatient-company-support.d1ed5f3f'
    assert policy.resolve(question+' nguồn '+labor).task_id=='task.topic.labor-health-benefits'


def test_slot_changes_preserve_leave_topic_and_do_not_invent_platform_support(policy):
    from src.conversation.context import reduce_question
    catalog,lexicon=load_catalog(REPOSITORY_ROOT/'artifacts/answers/catalog.json',REPOSITORY_ROOT/'artifacts/semantics/lexicon.json')
    _,state=reduce_question('Đăng ký nghỉ phép Workit',{},catalog,lexicon,policy=policy)
    question,state=reduce_question('không lương',state,catalog,lexicon,policy=policy)
    assert policy.resolve(question).task_id==state['task_id']=='task.leave.workit.unpaid'
    question,state=reduce_question('E-Leave',state,catalog,lexicon,policy=policy)
    assert policy.resolve(question).state=='unsupported' and state['task_id'] is None


def fictional_candidates(policy):
    keys = ['rank.eleave.3','rank.policy.16','rank.workit.request',
            'rank.workit.attendance','rank.workit.resignation']
    found, mappings = [], {}
    for n,key in enumerate(keys):
        binding = next(b for b in policy.catalog.bindings if b.id==key).model_copy(deep=True)
        text = f'Fictional excerpt {n}. All conditions must be retained.'
        doc, revision = (str(uuid5(NAMESPACE_URL, key+suffix)) for suffix in ('doc','revision'))
        metadata = dict(title='Fictional document',applicability='Fictional staff',effective_from=None,
                        effective_to=None,version_label='v1',source_path='fictional.txt')
        binding.ref = binding.ref.model_copy(update=dict(document_id=doc,revision_id=revision,chunk_index=n,
            source_sha256='a'*64,content_sha256=hashlib.sha256(text.encode()).hexdigest(),quote=text,metadata=metadata))
        match = dict(**metadata,document_id=doc,revision_id=revision,chunk_index=n,content=text,
                     chunk_id=f'fictional-{n}',run_id='fictional-run',source_sha256='a'*64,
                     cosine_distance=n/10,rerank_score=float(-n),source_spans=[{'page':1}],author_id=None,role=None)
        found.append(match)
        mappings[(doc,revision,n)] = binding
    policy._bindings = mappings
    return found


def test_order_uses_topic_role_and_system_without_rewriting_raw_logits(policy):
    matches = fictional_candidates(policy)
    raw = deepcopy(matches)
    ranked = policy.reorder('làm sao để đăng ký nghỉ 2 hôm.',matches)
    assert [m['chunk_id'] for m in ranked] == ['fictional-1','fictional-2','fictional-0','fictional-3','fictional-4']
    assert matches == raw
    assert policy.reorder('Xin nghỉ phép 2 ngày Workit',matches)[0]['chunk_id'] == 'fictional-2'
    assert policy.reorder('Xin nghỉ phép chiều thứ 6 và sáng thứ 7 E-Leave',matches)[0]['chunk_id'] == 'fictional-0'
    assert policy.reorder('Tôi muốn tìm mạng máy tính',matches) == raw
    assert policy.reorder('Xin nghỉ việc 2 hôm',matches) == raw
    assert policy.reorder('Xin thôi việc',matches) == raw
    # Cross-domain expansion now recognizes attendance too; the exact fictional
    # attendance reference is preferred without changing any model scores.
    assert policy.reorder('Đăng ký đi trễ Workit',matches)[0]['chunk_id'] == 'fictional-3'


@pytest.mark.parametrize('field,value', [('content','Changed fictional condition'),('source_sha256','b'*64),
    ('revision_id','old'),('version_label','v2'),('source_path','other.txt'),('applicability','Other audience')])
def test_stale_metadata_loses_priority_without_withholding_candidate(policy,field,value):
    matches = fictional_candidates(policy)
    matches[1][field] = value
    ranked = policy.reorder('Đăng ký nghỉ hai ngày',matches)
    assert ranked[0]['chunk_id'] == 'fictional-2'
    assert len(ranked)==len(matches) and ranked[2]['chunk_id']=='fictional-1'


def test_ties_keep_existing_order(policy):
    matches = fictional_candidates(policy)
    original = matches[2]
    twin = {**original,'chunk_id':'twin'}
    assert [m['chunk_id'] for m in policy.reorder('Đăng ký nghỉ trên Workit',[twin,original])] == ['twin',original['chunk_id']]


@pytest.mark.parametrize('kind', ['missing','invalid','mismatched','duplicate'])
def test_unavailable_policy_is_safe_and_does_not_require_a_profile(policy,tmp_path,caplog,kind):
    lexical = Path(policy.lexicon_path).read_bytes()
    path = tmp_path/'catalog.json'
    data = policy.catalog.model_dump()
    if kind=='invalid': path.write_text('PRIVATE_NOT_JSON',encoding='utf-8')
    elif kind=='mismatched':
        data['lexicon_sha256']='b'*64;path.write_text(json.dumps(data),encoding='utf-8')
    elif kind=='duplicate':
        data['bindings'].append(data['bindings'][0]);path.write_text(json.dumps(data),encoding='utf-8')
    lex = tmp_path/'lexicon.json';lex.write_bytes(lexical)
    p = RetrievalPolicy(path,lex);p.start()
    assert p.catalog is None and p.resolve('Xin nghỉ hai ngày').state=='not_applicable'
    assert p.reorder('Xin nghỉ hai ngày',[{'chunk_id':'unchanged'}])==[{'chunk_id':'unchanged'}]
    assert 'PRIVATE_NOT_JSON' not in caplog.text and 'Fictional excerpt' not in caplog.text


def test_recognition_only_concepts_do_not_rewrite_leave_words():
    from src.retrieval.semantics import _rules
    lexical = json.loads((REPOSITORY_ROOT/'artifacts/semantics/lexicon.json').read_bytes())
    assert not any(rule[0].startswith('query.') for rule in _rules(lexical))
    baseline = {**lexical,'concepts':[c for c in lexical['concepts'] if not c.get('recognition_only')]}
    for q in ('Xin nghỉ việc 2 hôm','Nghỉ phép không lương 2 ngày','Tôi nghỉ hai ngày'):
        assert expansion_candidates(lexical,q)==expansion_candidates(baseline,q)


def test_real_service_sorts_before_top_k_and_strips_internal_hash(policy,monkeypatch):
    matches = fictional_candidates(policy)
    async def scenario():
        service = RetrievalService(model_path='unused',model_revision='a'*40,
                                   rerank=RerankSettings(enabled=True),policy=policy)
        service._model = object()
        service._fingerprint = 'fictional'
        service._encode = Mock(return_value=[1.]+[0.]*383)
        service._reranker = SimpleNamespace(fingerprint='fake',score=lambda *a:([0.,-1.,-2.,-3.,-4.],5),close=Mock())
        monkeypatch.setattr(storage,'search',AsyncMock(return_value=dict(status='matches_found',matches=matches)))
        row = await service.retrieve('Xin nghỉ hai ngày',1)
        assert storage.search.call_args.args[3]==30
        assert row['matches'][0]['chunk_id']=='fictional-1'
        assert row['matches'][0]['rerank_score']==-1.
        assert 'source_sha256' not in json.dumps(row)
        assert row['relevance']['status']=='unassessed'
        await service.close()
    asyncio.run(scenario())


def test_http_answer_clarifies_without_reading_personnel_or_document_evidence(policy):
    from src.type_b.config import TypeBSettings
    from src.answering.config import AnswerSettings
    retrieval = SimpleNamespace(policy=policy,start=AsyncMock(),close=AsyncMock(),started=True,
        retrieve=AsyncMock(),read_evidence=AsyncMock())
    catalog,lexicon = load_catalog(REPOSITORY_ROOT/'artifacts/answers/catalog.json',REPOSITORY_ROOT/'artifacts/semantics/lexicon.json')
    employee = SimpleNamespace(check_source=AsyncMock(),close=AsyncMock(),find_by_user_id=AsyncMock())
    owner = ApiService(Settings(Path('unused'),'a'*40,type_b=TypeBSettings(True),
                                answer=AnswerSettings(enabled=True), service_key=SERVICE_KEY),retrieval=retrieval,
                       employee_service=employee,database_check=AsyncMock(),database_close=AsyncMock())
    owner.started=True;owner.answers.available=True
    owner.answers.catalog,owner.answers.lexicon=catalog,lexicon
    with TestClient(create_app(owner.settings,service_factory=lambda _:owner), headers=SERVICE_HEADERS) as client:
        response=client.post('/api/v1/answer',json=dict(text='làm sao để đăng ký nghỉ 2 hôm.',hrm_user_id=22,top_k=5),
                             headers=SERVICE_HEADERS)
    assert response.status_code==200
    body=response.json()
    assert body['status']=='needs_clarification' and body['fallback_reason']=='leave_needs_clarification'
    assert '2 ngày' in body['answer'] and 'Workit hay E-Leave' in body['answer']
    assert body['claims']==body['sources']==[]
    retrieval.retrieve.assert_not_awaited();retrieval.read_evidence.assert_not_awaited()
    employee.find_by_user_id.assert_not_awaited()


def test_known_existing_eleave_fields_task_keeps_its_contract(policy):
    catalog,lexicon=load_catalog(REPOSITORY_ROOT/'artifacts/answers/catalog.json',REPOSITORY_ROOT/'artifacts/semantics/lexicon.json')
    baseline,ambiguous=select_cards('Trường bắt buộc E-Leave',catalog,lexicon)
    selected,now_ambiguous=select_cards('Trường bắt buộc E-Leave',catalog,lexicon,policy)
    assert not ambiguous and not now_ambiguous
    assert [c.id for c in selected]==[c.id for c in baseline]
    assert policy.resolve('Tạo phiếu nghỉ việc Workit').task_id=='task.bde3f581823256b483a01d2ce3b9926e'


def fake_runtime(policy, monkeypatch):
    matches=fictional_candidates(policy)
    service=RetrievalService(model_path='unused',model_revision='a'*40,rerank=RerankSettings(enabled=True),policy=policy)
    service._model=object();service._fingerprint='fictional'
    service._encode=Mock(return_value=[1.]+[0.]*383)
    service._reranker=SimpleNamespace(fingerprint='fake',score=lambda *a:([0.,-1.,-2.,-3.,-4.],5),close=Mock())
    monkeypatch.setattr(storage,'search',AsyncMock(return_value=dict(status='matches_found',matches=matches)))
    return service


def test_http_retrieve_serializes_ranked_top_k_without_internal_hash(policy,monkeypatch):
    retrieval=fake_runtime(policy,monkeypatch)
    owner=ApiService(Settings(Path('unused'),'a'*40, service_key=SERVICE_KEY),retrieval=retrieval,
                     database_check=AsyncMock(),database_close=AsyncMock())
    owner.started=True
    with TestClient(create_app(owner.settings,service_factory=lambda _:owner), headers=SERVICE_HEADERS) as client:
        response=client.post('/api/v1/retrieve',json=dict(text='Đăng ký nghỉ hai ngày',top_k=1))
    assert response.status_code==200
    body=response.json()
    assert set(body)=={'status','matches','fallback_reason','relevance','timings'}
    assert [m['chunk_id'] for m in body['matches']]==['fictional-1']
    assert body['matches'][0]['rerank_score']==-1.
    assert body['relevance']['status']=='unassessed'
    assert 'source_sha256' not in json.dumps(body) and 'bindings' not in body


def test_cli_serializes_the_same_candidate_policy(policy,monkeypatch,capsys):
    from src.cli import retrieve_documents as cli
    retrieval=fake_runtime(policy,monkeypatch)
    monkeypatch.setattr('src.retrieval.runner.RetrievalService',lambda **kw:retrieval)
    monkeypatch.setattr('src.database.close_database',AsyncMock())
    assert cli.main(['--question','Đăng ký nghỉ hai ngày','--model-path','unused',
                     '--model-revision','a'*40,'--top-k','1','--json'])==0
    body=json.loads(capsys.readouterr().out)
    assert [m['chunk_id'] for m in body['matches']]==['fictional-1']
    assert body['relevance']['status']=='unassessed' and 'source_sha256' not in json.dumps(body)


@pytest.mark.parametrize('available',[True,False])
def test_fully_specified_leave_uses_bound_evidence_and_never_personal_eligibility(policy,available):
    matches=fictional_candidates(policy)
    binding=next(b for b in policy._bindings.values() if b.id=='rank.policy.16')
    unit=dict(id='evidence.fictional.leave',text=binding.ref.quote,refs=[binding.ref.model_dump()])
    catalog=AnswerCatalog.model_validate(dict(schema_version=2,package_fingerprint='fictional',lexicon_sha256='fictional',
        cards=[dict(id='task.leave.workit.unpaid',source_type='type_a',label='Fictional leave task',
            clarification='Fictional clarification',concept_ids=[],aliases=['fictional-leave'],requires_any=[],excludes_any=[],
            sections={'procedure':dict(complete=False,units=[unit])})],coverage=[]))
    result=dict(status='matches_found' if available else 'no_matches',matches=[matches[1]] if available else [],
                fallback_reason=None if available else 'no_candidates',
                relevance=dict(status='unassessed' if available else 'no_candidates',warnings=['relevance_not_assessed']))
    retrieval=SimpleNamespace(policy=policy,read_evidence=AsyncMock(return_value=result),retrieve=AsyncMock())
    row=asyncio.run(evaluate_question('Cách xin nghỉ không lương 2 ngày Workit',retrieval,catalog,{'concepts':[]}))
    retrieval.retrieve.assert_not_awaited();retrieval.read_evidence.assert_awaited_once()
    assert row['status']==('partial' if available else 'insufficient_evidence')
    assert bool(row['claims']) is available
    if available: assert row['claims'][0]['citations'][0]['quote']==binding.ref.quote


def test_private_benchmark_input_is_bounded_and_never_loads_a_database(tmp_path,monkeypatch):
    from scripts import benchmark_leave_retrieval as benchmark
    monkeypatch.setattr(benchmark,'REPOSITORY_ROOT',tmp_path)
    folder=tmp_path/'.tmp';folder.mkdir();path=folder/'cases.json'
    path.write_text(json.dumps([dict(id='fictional',text='Xin nghỉ hai ngày',expected_role='general_procedure')]),encoding='utf-8')
    assert len(benchmark.load_cases(path))==1
    with pytest.raises(ValueError):benchmark.private_path(tmp_path/'outside.json')
    path.write_text(json.dumps([dict(id='fictional',text=' ',expected_role='general_procedure')]),encoding='utf-8')
    with pytest.raises(ValueError):benchmark.load_cases(path)


def add_retrieval_sources(root, policy):
    # Test third-group build/import/refresh with fictional references only.
    raw, _=sources.assemble(root)
    answer=sources.decode(raw[1]);first=answer['cards'][0]['sections']['overview']['units'][0]['refs'][0]
    lexical=sources.decode(raw[0]);concept=lexical['concepts'][0]['id']
    index=dict(schema_version=1,metadata=dict(schema_version=1,package_fingerprint=answer['package_fingerprint']))
    for collection in sources.GROUPS['retrieval'][1]:
        rows=[]
        if collection=='families':rows=[dict(id='leave',concept_ids=[concept])]
        if collection=='bindings':
            b=policy.catalog.bindings[0].model_dump();b.update(id='fictional',ref=first,task_ids=[answer['cards'][0]['id']]);rows=[b]
        if collection=='routes':rows=[dict(id='fictional',subtype='annual',operation='request',system='workit',task_id=answer['cards'][0]['id'])]
        index[collection]=[]
        for row in rows:
            relative=collection+'/'+row['id']+'.json';path=root/'artifacts/retrieval/source'/relative
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(sources.encode(row));index[collection].append(relative)
    (root/'artifacts/retrieval/source/index.json').write_bytes(sources.encode(index))


def test_three_group_build_import_and_dependency_local_refresh(authored,policy):
    root,paths,_=authored
    add_retrieval_sources(root,policy)
    assert workflow.build(root)['changed_files']==1
    assert workflow.build(root,check=True)['status']=='current'
    output=root/'.tmp/three-import'
    workflow.import_pair(*paths,output,root,retrieval_path=root/'artifacts/retrieval/catalog.json')
    assert sources.assemble(output)[0]==sources.assemble(root)[0]
    package=next((root/'chunks').iterdir());path=package/'chunks.json';data=json.loads(path.read_bytes())
    data['chunks'].reverse();path.write_bytes(sources.encode(data))
    report=impact.inspect(root)[0]
    assert report['summary']=={'relinkable':3}
    impact.refresh(root);workflow.build(root)
    binding=json.loads((root/'artifacts/retrieval/catalog.json').read_bytes())['bindings'][0]
    assert binding['ref']['chunk_index']==1
    assert impact.inspect(root)[0]['summary']=={'unchanged':3}
