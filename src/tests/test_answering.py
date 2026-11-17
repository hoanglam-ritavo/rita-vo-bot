"""Source-bound template and HTTP regressions with fictional evidence only."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from src.answering.catalog import Catalog, validate_catalog, METADATA
from src.answering.config import AnswerSettings, from_environment
from src.answering.pipeline import render, evaluate_question, evidence_sources
from src.api.app import create_app
from src.api.config import Settings
from src.api.contracts import ApiError
from src.api.service import ApiService
from src.retrieval import RetrievalError


@pytest.fixture
def sample():
    text = 'Fictional policy: permanent staff receive 100 credits with a certificate; temporary staff excluded.'
    metadata = dict(title='Fictional policy', applicability='Permanent staff', effective_from=None,
                    effective_to=None, version_label='v1', source_path='fictional.pdf')
    match = dict(content=text, **metadata, document_id='doc', revision_id='revision', chunk_index=0,
                 chunk_id='chunk', run_id='run', source_spans=[{'page': 1}])
    ref = dict(document_id='doc', revision_id='revision', chunk_index=0,
               content_sha256=hashlib.sha256(text.encode()).hexdigest(), quote=text, metadata=metadata)
    unit = dict(text='Permanent staff receive 100 credits with a certificate; temporary staff are excluded.', refs=[ref])
    card = dict(id='birth', label='Fictional childbirth benefit', concept_ids=['birth'],
                aliases=['sinh con'], requires_any=[], excludes_any=['vợ', 'Workit'],
                sections={'overview':dict(complete=False, units=[unit]), 'amount':dict(complete=True, units=[unit]),
                          'conditions':dict(complete=True, units=[unit]), 'paperwork':dict(complete=True, units=[unit])})
    data = dict(schema_version=1, package_fingerprint='packages', lexicon_sha256='lexicon', cards=[card],
                coverage=[dict(document_id='doc',chunk_index=0,disposition='template',note='Fictional source checked.')])
    lexicon = dict(package_fingerprint='packages', concepts=[dict(id='birth', canonical='sinh con',
        aliases=['sinh em bé'], requires_any=[], excludes_any=['vợ', 'Workit'])])
    result = dict(status='matches_found', matches=[match], fallback_reason=None,
                  relevance=dict(status='relevant_candidates', warnings=[]))
    return SimpleNamespace(data=data, catalog=Catalog.model_validate(data), lexicon=lexicon, result=result,
                           metadata=metadata)


def test_template_aspects_and_partial_overview(sample):
    row = render('Sinh em bé được bao nhiêu và cần giấy tờ gì?', sample.result, sample.catalog, sample.lexicon)
    assert row['status'] == 'answered' and len(row['claims']) == 1
    assert 'certificate' in row['claims'][0]['text']
    assert row['claims'][0]['citations'][0]['quote'] == sample.result['matches'][0]['content']
    assert render('Hỗ trợ sinh con?', sample.result, sample.catalog, sample.lexicon)['status'] == 'partial'


@pytest.mark.parametrize('mutation', ['revision_id','chunk_index','content','applicability','effective_from','source_path'])
def test_source_mismatch_never_uses_authored_text(sample, mutation):
    sample.result['matches'][0][mutation] = 9 if mutation == 'chunk_index' else 'CHANGED'
    row = render('Sinh con được bao nhiêu?', sample.result, sample.catalog, sample.lexicon)
    assert row['reason'] == 'template_evidence_missing'
    assert row['intro'] and row['claims'][0]['text'] == sample.result['matches'][0]['content']


def test_all_required_sources_or_partial(sample):
    data = deepcopy(sample.data)
    data['cards'][0]['sections']['amount']['units'][0]['refs'].append(
        {**data['cards'][0]['sections']['amount']['units'][0]['refs'][0], 'chunk_index':1})
    row = render('Sinh con được bao nhiêu?', sample.result, Catalog.model_validate(data), sample.lexicon)
    assert row['reason'] == 'template_evidence_missing'


@pytest.mark.parametrize('text', ['Vợ sinh con?', 'Trường hợp của tôi có được hỗ trợ không?',
    'Nhân viên không sinh con có được hỗ trợ?', 'Tổng phúc lợi bao nhiêu?', 'So sánh hai chính sách?'])
def test_ambiguity_and_guards(sample,text):
    row=render(text,sample.result,sample.catalog,sample.lexicon)
    assert row['status'] in {'needs_clarification','partial'}
    assert row['status'] != 'answered'
    if row['status']=='partial': assert row['reason']=='template_unavailable'


def test_multiple_cards_do_not_choose_top_one(sample):
    data=deepcopy(sample.data)
    data['cards'].append({**data['cards'][0],'id':'other'})
    row=render('Sinh con được bao nhiêu?',sample.result,Catalog.model_validate(data),sample.lexicon)
    assert row['status']=='needs_clarification' and row['claims']==[]


def test_conjunction_preserves_two_beneficiaries_despite_global_exclusion(sample):
    data=deepcopy(sample.data)
    data['cards'][0]['excludes_any']=['con nhân viên']
    child=deepcopy(data['cards'][0])
    child.update(id='child',aliases=['con nhân viên sinh con'],concept_ids=[],excludes_any=[])
    data['cards'].append(child)
    catalog=Catalog.model_validate(data)
    row=render('Nhân viên sinh con và con nhân viên sinh con được bao nhiêu?',
               sample.result,catalog,sample.lexicon)
    assert row['status']=='needs_clarification' and row['claims']==[]
    assert render('Con nhân viên sinh con được bao nhiêu?',sample.result,catalog,
                  sample.lexicon)['status']=='answered'


def test_unassessed_preserved_and_filter_never_bypassed(sample):
    sample.result['relevance']={'status':'unassessed','warnings':['relevance_filter_disabled']}
    row=render('Sinh con được bao nhiêu?',sample.result,sample.catalog,sample.lexicon,allow_unassessed=True)
    assert row['status']=='answered' and row['relevance']['status']=='unassessed'
    assert 'evidence_completeness_not_assessed' in row['relevance']['warnings']
    with pytest.raises(ValueError): render('Question',sample.result,sample.catalog,sample.lexicon)
    sample.result.update(matches=[],fallback_reason='relevance_unavailable')
    sample.result['relevance']={'status':'unassessed','warnings':[]}
    row=render('Sinh con được bao nhiêu?',sample.result,sample.catalog,sample.lexicon)
    assert row['status']=='insufficient_evidence' and row['sources']=={}


def test_excerpts_are_verbatim_bounded_and_keep_provenance(sample):
    sample.result['matches']*=20
    sample.result['matches']=[dict(m,chunk_id=str(i),content=m['content']+str(i))
                              for i,m in enumerate(sample.result['matches'])]
    row=render('Unrecognized question?',sample.result,sample.catalog,sample.lexicon)
    assert row['status']=='partial' and len(row['claims'])==12 and row['intro']
    assert all(c['text']==c['citations'][0]['quote'] for c in row['claims'])
    assert len(row['limitations'])==2
    duplicate=evidence_sources([sample.result['matches'][0],dict(sample.result['matches'][0],chunk_id='copy')])
    assert len(duplicate)==1 and len(duplicate['S1']['origins'])==2


def service(sample):
    owner=ApiService(Settings(Path('unused'),'a'*40),retrieval=SimpleNamespace(
        start=AsyncMock(),close=AsyncMock(),started=True,retrieve=AsyncMock(return_value=sample.result)),
        database_check=AsyncMock(),database_close=AsyncMock())
    owner.started=True
    owner.answers.available=True
    owner.answers.catalog,owner.answers.lexicon=sample.catalog,sample.lexicon
    return owner


@pytest.mark.parametrize('top_k',[None,3])
def test_http_wire_sources_no_qwen_and_default_query(sample,top_k,monkeypatch,caplog,capsys):
    import builtins
    original=builtins.__import__
    def guard(name,*args,**kwargs):
        if name.startswith('llama_cpp'): raise AssertionError('No GGUF imports allowed')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guard)
    owner=service(sample)
    owner.settings=replace(owner.settings,top_k=7)
    owner.answers.settings=owner.settings
    with TestClient(create_app(owner.settings,service_factory=lambda _:owner)) as client:
        body={'text':'Sinh con được bao nhiêu?'}
        if top_k: body['top_k']=top_k
        response=client.post('/api/v1/answer',json=body)
    assert response.status_code==200
    data=response.json()
    assert data['status']=='answered' and '[S1]' in data['answer']
    assert data['sources'][0]['origins'][0]['chunk_id']=='chunk'
    assert data['timings']['generation_seconds']==data['timings']['verification_seconds']==0
    assert not {'draft','verdict','confidence','request_id'} & data.keys()
    owner.retrieval.retrieve.assert_awaited_once_with(body['text'],top_k=top_k or 7)
    assert not capsys.readouterr().out
    assert 'certificate' not in caplog.text


def test_busy_timeout_errors_and_cleanup(sample,monkeypatch):
    import src.api.answer as module
    async def run():
        owner=service(sample)
        owner.busy=True
        with pytest.raises(ApiError) as exc: await owner.answers.answer('Question',5)
        assert exc.value.status_code==429
        owner.busy=False
        owner.retrieval.retrieve.side_effect=RetrievalError('question_too_long')
        with pytest.raises(ApiError) as exc: await owner.answers.answer('Question',5)
        assert exc.value.status_code==422 and not owner.busy and owner._idle.is_set()
        owner.retrieval.retrieve.side_effect=None
        async def wait(*args, **kwargs): await asyncio.sleep(10)
        owner.retrieval.retrieve.side_effect=wait
        monkeypatch.setattr(module,'DEADLINE_SECONDS',.01)
        with pytest.raises(ApiError) as exc: await owner.answers.answer('Question',5)
        assert exc.value.status_code==504 and not owner.busy
        assert owner.answers.available  # No uncertain native context to invalidate.
    asyncio.run(run())


def test_cancel_and_repeated_drain_retains_ownership(sample):
    async def run():
        owner=service(sample)
        entered,finish=asyncio.Event(),asyncio.Event()
        async def retrieve(*args, **kwargs):
            entered.set()
            worker=asyncio.create_task(finish.wait())
            try: await asyncio.shield(worker)
            except asyncio.CancelledError:
                from src.api.answer import drain
                await drain(worker)
                raise
        owner.retrieval.retrieve.side_effect=retrieve
        task=asyncio.create_task(owner.answers.answer('Question',5))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        assert owner.busy
        finish.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert not owner.busy and owner._idle.is_set()
    asyncio.run(run())


@pytest.mark.parametrize('failure',[False,True])
def test_catalog_startup_and_role_isolation(sample,monkeypatch,failure):
    from src.retrieval.semantics import SemanticSettings
    import src.api.answer as module
    def load(*args):
        if failure: raise ValueError('PRIVATE')
        return sample.catalog,sample.lexicon
    monkeypatch.setattr(module,'load_catalog',load)
    owner=service(sample)
    owner.settings=replace(owner.settings,answer=AnswerSettings(enabled=True),
                           semantic=SemanticSettings('expand',Path('fictional')))
    owner.answers.settings=owner.settings
    async def run():
        await owner.answers.start()
        assert owner.answers.available is not failure
        await owner.answers.close()
        assert owner.answers.catalog is None
    asyncio.run(run())


def test_config_retired_keys_cannot_load_models():
    settings=from_environment({'ANSWER_ENABLED':'true','ANSWER_MODEL_PATH':'PRIVATE',
        'ANSWER_MODEL_SHA256':'bad','ANSWER_REPORT_PATH':'bad','ANSWER_DEBUG_RAW':'invalid'})
    assert settings.enabled and not hasattr(settings,'model_path') and not hasattr(settings,'debug_raw')
    with pytest.raises(ValueError): from_environment({'ANSWER_ENABLED':'yes'})
    assert AnswerSettings().enabled is False
