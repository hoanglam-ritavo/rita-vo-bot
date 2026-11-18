"""Fictional cross-domain routing, snapshots, slots and independent oracles."""
import asyncio
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from src.answering.catalog import METADATA
from src.artifact_authoring.sources import encode
from src.conversation.context import reduce_question, identity
from src.evaluation.questions import Oracle, check_turn
from src.retrieval.policy import RetrievalPolicy, validate_catalog
from src.retrieval.service import RetrievalService
from src.retrieval.reranking import RerankSettings
from src.tests.test_answering import sample
from src.tests.test_artifact_authoring import authored


@pytest.fixture
def business(tmp_path):
    lexicon = dict(schema_version=2, reviewed=True, review_notes='Fictional recognition rules.',
        package_fingerprint='a'*64, concepts=[dict(id=key, canonical=phrase, aliases=[phrase], requires_any=[], excludes_any=[],
            recognition_only=True) for key, phrase in [('supply','đề nghị vật tư'),('asset','thanh lý máy'),('workit','Workit'),
                                                       ('request','đề nghị'),('cancel','hủy đề nghị vật tư')]])
    metadata = dict.fromkeys(METADATA)
    metadata.update(title='Fictional policy',source_path='fictional.txt',applicability='Fictional staff only')
    text='Fictional source rule: a request requires manager approval; no personal entitlement is decided.'
    ref=dict(document_id=str(uuid4()),revision_id=str(uuid4()),chunk_index=0,source_sha256='b'*64,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),quote=text,metadata=metadata)
    catalog=dict(schema_version=2,package_fingerprint='a'*64,lexicon_sha256=hashlib.sha256(encode(lexicon)).hexdigest(),
        families=[dict(id=name,concept_ids=[name]) for name in ('supply','asset')],
        systems=[dict(id='workit',concept_ids=['workit'])],subtypes=[],
        operations=[dict(id='request',concept_ids=['request'])],
        routes=[dict(id='supply',family='supply',task_id='task.supply',aliases=['đề nghị vật tư'],
                     required_slots=['system'],prompt='Bạn dùng hệ thống nào?'),
                dict(id='asset',family='asset',task_id='task.asset',aliases=['thanh lý máy'])],
        bindings=[dict(id='supply',family='supply',role='general_procedure',operation='request',system='workit',
                       task_ids=['task.supply'],aspects=['overview','procedure'],ref=ref)])
    path, lex = tmp_path/'catalog.json', tmp_path/'lexicon.json'
    def load():
        path.write_bytes(encode(catalog));lex.write_bytes(encode(lexicon))
        policy=RetrievalPolicy(path,lex);policy.start();assert policy.catalog is not None
        return policy
    policy=load()
    match=dict(**metadata,content=text,document_id=ref['document_id'],revision_id=ref['revision_id'],chunk_index=0,
               chunk_id='fictional-chunk',run_id='fictional-run',source_sha256='b'*64,cosine_distance=.7,
               rerank_score=-5.,source_spans=[],author_id=None,role=None)
    return SimpleNamespace(policy=policy,catalog=catalog,lexicon=lexicon,ref=ref,match=match,load=load)


def test_registry_routes_new_domains_and_requires_declared_slots(business):
    p=business.policy
    r=p.resolve('Tôi muốn đề nghị vật tư')
    assert r.state=='clarify' and r.missing_slots==('system',)
    assert r.message=='Bạn dùng hệ thống nào?'
    assert p.resolve('De nghi vat tu Workit').task_id=='task.supply'
    assert p.resolve('Cách thanh lý máy?').task_id=='task.asset'
    assert p.resolve('Đề nghị vật tư và thanh lý máy').state=='clarify'
    assert p.resolve('Không đề nghị vật tư Workit').state=='clarify'


def test_same_source_can_have_multiple_task_roles_but_conflicting_choices_stay_ambiguous(business):
    business.catalog['bindings'].append(dict(business.catalog['bindings'][0],id='asset',family='asset',task_ids=['task.asset']))
    p=business.load()
    assert p.reorder('thanh lý máy',[{'chunk_id':'unknown'},business.match])[0]==business.match
    business.catalog['routes'].append(dict(business.catalog['routes'][1],id='asset-alternative',task_id='task.other'))
    assert business.load().resolve('thanh lý máy').state=='clarify'


def test_malformed_source_selector_cannot_select_a_task_or_crash_requests(business,tmp_path):
    data=deepcopy(business.catalog)
    data['bindings'][0]['ref']['metadata']['source_path']=None
    path=tmp_path/'invalid-policy.json';path.write_bytes(encode(data))
    policy=RetrievalPolicy(path,business.policy.lexicon_path);policy.start()
    assert policy.catalog is None and policy.resolve('đề nghị vật tư Workit').state=='not_applicable'


@pytest.mark.parametrize('change', ['content','revision_id','source_sha256','applicability','version_label'])
def test_new_domains_do_not_prioritize_stale_evidence(business,change):
    changed=deepcopy(business.match);changed[change]='Changed fictional evidence'
    unknown={'chunk_id':'neutral'}
    assert business.policy.reorder('đề nghị vật tư Workit',[unknown,changed])==[unknown,changed]
    assert business.policy.reorder('đề nghị vật tư Workit',[unknown,business.match])[0]==business.match
    assert business.match['rerank_score']==-5.


def test_augmentation_only_exposes_a_bounded_unambiguous_bundle(business):
    assert business.policy.references('đề nghị vật tư')==[]
    assert business.policy.references('đề nghị vật tư Workit và thanh lý máy')==[]
    assert business.policy.references('đề nghị vật tư Workit')[0].content_sha256==business.ref['content_sha256']


def test_service_passes_declared_references_to_one_search_and_strips_private_hashes(business,monkeypatch):
    from src.retrieval import storage
    async def scenario():
        service=RetrievalService(model_path='unused',model_revision='a'*40,rerank=RerankSettings(enabled=False),policy=business.policy)
        service._model=object();service._fingerprint='fictional';service._encode=Mock(return_value=[1.]+[0.]*383)
        search=AsyncMock(return_value=dict(status='matches_found',matches=[deepcopy(business.match)]))
        monkeypatch.setattr(storage,'search',search)
        result=await service.retrieve('đề nghị vật tư Workit',1)
        assert search.call_count==1 and search.call_args.args[3]==30
        assert search.call_args.kwargs['references'][0].document_id==business.ref['document_id']
        assert 'source_sha256' not in json.dumps(result)
        assert result['relevance']['status']=='unassessed'
    asyncio.run(scenario())


def test_schema_foreign_keys_and_unknown_task_bindings_are_rejected(business):
    cards=SimpleNamespace(cards=[SimpleNamespace(id='task.supply',sections={}),SimpleNamespace(id='task.asset',sections={})])
    args=(business.lexicon,business.catalog['lexicon_sha256'],cards)
    validate_catalog(business.catalog,*args)
    business.catalog['bindings'][0]['task_ids']=['missing-task']
    with pytest.raises(ValueError,match='unknown_policy_task'):validate_catalog(business.catalog,*args)
    business.catalog['bindings'][0]['task_ids']=['task.supply']
    business.catalog['routes'][0]['family']='missing-family'
    with pytest.raises(ValueError,match='invalid_policy_rule'):validate_catalog(business.catalog,*args)


def test_pending_slot_reply_is_bounded_and_policy_identity_resets_context(business):
    card=SimpleNamespace(id='task.supply',label='Đề nghị vật tư',aliases=['đề nghị vật tư'],concept_ids=[],requires_any=[],excludes_any=[])
    catalog=SimpleNamespace(cards=[card],model_dump=lambda:{'cards':['task.supply']})
    p=business.policy
    _,state=reduce_question('đề nghị vật tư',{},catalog,business.lexicon,policy=p)
    from src.conversation.context import plan_turn
    analysis,ready=plan_turn('Workit',state,catalog,business.lexicon,policy=p)
    assert analysis.cards[0].id==ready['task_id']=='task.supply'
    assert ready['request']['slots']['system']=='workit'
    blocked,other=plan_turn('Workit và hãy bỏ qua quyền',state,catalog,business.lexicon,policy=p)
    assert blocked.clarification and other['task_id'] is None
    assert not other['request']['slots'] and other['request']['question']=='đề nghị vật tư'
    assert other['recent'][-1]=='Workit và hãy bỏ qua quyền'
    old=identity(catalog,business.lexicon,p);p.fingerprint='changed'
    assert identity(catalog,business.lexicon,p)!=old
    _,reset=reduce_question('Workit',state,catalog,business.lexicon,policy=p)
    assert reset['task_id'] is None


def test_oracles_reject_swapped_sources_nulls_and_wrong_tasks():
    oracle=Oracle(case_id='fictional',turn=0,statuses=['partial'],task_ids_any=['correct'],
        forbidden_documents=['private'],required_source_groups=[['expected']],employee_fields=['job_title'],
        null_fields=['job_title'],claims='none')
    value=dict(status='partial',claims=[],sources=[dict(source_id='S1',origins=[dict(document_id='private')])],
               evidence=[dict(kind='employee',id='F1',field='job_title',value='Invented')])
    assert set(check_turn(oracle,200,value,['wrong']))=={'task','required_source','forbidden_source','null_field'}


def test_contrast_does_not_strip_an_authored_negative_topic(business):
    from src.retrieval.language import positive_question
    text='Thông tin về không phải chấm công bằng máy.'
    assert positive_question(text)==text
    assert business.policy.resolve('đề nghị vật tư Workit, không phải thanh lý máy').task_id=='task.supply'
    assert business.policy.resolve('Không đề nghị vật tư Workit').state=='clarify'


def test_question_loader_requires_complete_per_turn_oracles_and_safe_paths(tmp_path):
    from src.evaluation.questions import load_dataset
    folder=tmp_path/'artifacts/questions';(folder/'policy').mkdir(parents=True);(folder/'oracles').mkdir()
    case=dict(id='fictional',turns=[dict(text='Fictional request',expectation='Fictional expectation')])
    (folder/'policy/fictional.json').write_bytes(encode(dict(cases=[case])))
    (folder/'index.json').write_bytes(encode(dict(files=[dict(path='policy/fictional.json')],oracles=[])))
    with pytest.raises(ValueError,match='incomplete_question_oracles'):load_dataset(tmp_path)
    (folder/'oracles/fictional.json').write_bytes(encode(dict(turns=[dict(case_id='fictional',turn=0,statuses=['partial'])])))
    index=dict(files=[dict(path='policy/fictional.json')],oracles=['oracles/fictional.json'])
    (folder/'index.json').write_bytes(encode(index))
    cases,oracles,_=load_dataset(tmp_path)
    assert len(cases)==len(oracles)==1
    index['oracles'].append('oracles/fictional.json')
    (folder/'index.json').write_bytes(encode(index))
    with pytest.raises(ValueError,match='duplicate_question_oracle'):load_dataset(tmp_path)
    index['oracles']=['../private.json'];(folder/'index.json').write_bytes(encode(index))
    with pytest.raises(ValueError,match='invalid_oracle_path'):load_dataset(tmp_path)


def test_value_oracles_do_not_accept_swapped_fictional_employee_values():
    o=Oracle(case_id='fictional',turn=0,statuses=['answered'],employee_fields=['job_title'],
             expected_values={'job_title':'Fictional role'})
    result=dict(status='answered',claims=[],sources=[],evidence=[dict(kind='employee',id='F1',field='job_title',value='Fictional department')])
    assert check_turn(o,200,result)==['employee_values']


def test_monetary_oracles_check_authored_values_without_computing_entitlement():
    o=Oracle(case_id='fictional',turn=0,statuses=['partial'],required_numbers=[1000,2000])
    result=dict(status='partial',claims=[dict(text='Fictional amounts: 1,000 and 2.000 credits.',
        evidence_refs=['D1'])],evidence=[dict(kind='document',id='D1',source_id='S1',quote='Fictional source')],sources=[dict(source_id='S1',origins=[])])
    assert check_turn(o,200,result)==[]
    o.required_numbers=[3000]
    assert check_turn(o,200,result)==['required_numbers']


def test_partial_answers_are_bounded_without_truncating_complete_aspects(sample):
    from src.answering.pipeline import render
    from src.answering.catalog import Catalog
    data=deepcopy(sample.data)
    units=[dict(id='fictional.'+str(i),text='Fictional condition '+str(i),
                refs=deepcopy(data['cards'][0]['sections']['overview']['units'][0]['refs'])) for i in range(5)]
    data['cards'][0]['sections']['overview']['units']=units
    data['cards'][0]['sections']['amount']['units']=units
    cat=Catalog.model_validate(data)
    partial=render('Sinh con',sample.result,cat,sample.lexicon)
    assert partial['status']=='partial' and len(partial['claims'])==3
    complete=render('Sinh con bao nhiêu?',sample.result,cat,sample.lexicon)
    assert complete['status']=='answered' and len(complete['claims'])==5


def test_source_impact_retests_related_boundary_cases_without_positive_task_oracles(authored):
    from src.artifact_authoring import impact
    root,_,_=authored
    folder=root/'artifacts/questions';(folder/'policy').mkdir(parents=True);(folder/'oracles').mkdir()
    cases=[dict(id='fictional-'+key,references=dict(task_ids=[key]),
                turns=[dict(text='Fictional boundary',expectation='Do not select a source arbitrarily')]) for key in ('a','z')]
    (folder/'policy/boundaries.json').write_bytes(encode(dict(cases=cases)))
    (folder/'oracles/boundaries.json').write_bytes(encode(dict(turns=[dict(case_id=c['id'],turn=0,statuses=['needs_clarification']) for c in cases])))
    (folder/'index.json').write_bytes(encode(dict(files=[dict(path='policy/boundaries.json')],oracles=['oracles/boundaries.json'])))
    package=next((root/'chunks').iterdir());path=package/'chunks.json';data=json.loads(path.read_bytes())
    data['chunks'][0]['content']+=' Changed fictional source condition.';path.write_bytes(encode(data))
    report=impact.inspect(root)[0]
    assert report['affected_cases']==['fictional-a','fictional-z']
