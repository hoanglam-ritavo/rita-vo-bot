"""Source-bound template and HTTP regressions with fictional evidence only."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
import httpx
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


async def http_answer(app, incoming, watchers):
    """Exercise the ASGI route, including Request.is_disconnected and cleanup."""
    incoming.put_nowait({'type': 'http.request', 'body': json.dumps(
        {'text': 'Sinh con được bao nhiêu?'}).encode('utf-8'), 'more_body': False})
    messages = []

    async def receive():
        try:
            return incoming.get_nowait()
        except asyncio.QueueEmpty:
            watchers.add(asyncio.current_task())
            return await incoming.get()

    async def send(message):
        messages.append(message)

    await app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
               'method': 'POST', 'scheme': 'http', 'path': '/api/v1/answer',
               'raw_path': b'/api/v1/answer', 'query_string': b'',
               'headers': [(b'content-type', b'application/json')],
               'client': ('127.0.0.1', 1234), 'server': ('test', 80), 'root_path': ''},
              receive, send)
    status = next(m['status'] for m in messages if m['type'] == 'http.response.start')
    body = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
    return status, json.loads(body)


def hold_retrieval(owner, result):
    """A worker that acknowledges cancellation but owns resources until released."""
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def retrieve(*args, **kwargs):
        from src.api.answer import drain
        worker = asyncio.create_task(release.wait())
        entered.set()
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled.set()
            await drain(worker)
            raise
        return result

    owner.retrieval.retrieve.side_effect = retrieve
    return entered, cancelled, release


@pytest.mark.parametrize('termination', ['disconnect', 'timeout'])
def test_http_answer_drains_worker_and_watcher_before_releasing_busy(sample, monkeypatch, termination):
    async def run():
        owner = service(sample)
        app = create_app(owner.settings, service_factory=lambda _: owner)
        entered, cancelled, release = hold_retrieval(owner, sample.result)
        incoming, watchers = asyncio.Queue(), set()
        if termination == 'timeout':
            monkeypatch.setattr('src.api.answer.DEADLINE_SECONDS', .01)
        async with app.router.lifespan_context(app):
            task = asyncio.create_task(http_answer(app, incoming, watchers))
            try:
                await entered.wait()
                if termination == 'disconnect':
                    incoming.put_nowait({'type': 'http.disconnect'})
                await cancelled.wait()
                assert owner.busy and not owner._idle.is_set() and not task.done()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                            base_url='http://test') as client:
                    assert (await client.get('/health/live')).status_code == 200
                    assert (await client.get('/health/ready')).status_code == 200
                    assert (await client.post('/api/v1/retrieve', json={'text': 'Second'})).status_code == 429
                    assert (await client.post('/api/v1/answer', json={'text': 'Second'})).status_code == 429
                    release.set()
                    if termination == 'disconnect':
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    else:
                        status, body = await task
                        assert status == 504 and body['error']['code'] == 'answer_timeout'
                    assert watchers and all(w.done() for w in watchers)
                    assert not owner.busy and owner._idle.is_set() and owner.answers.available
                    owner.retrieval.retrieve.side_effect = None
                    response = await client.post('/api/v1/answer', json={'text': 'Sinh con được bao nhiêu?'})
                    assert response.status_code == 200 and response.json()['status'] == 'answered'
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        owner.retrieval.close.assert_awaited_once()
        owner.database_close.assert_awaited_once()

    async def bounded():
        async with asyncio.timeout(5):
            await run()
    asyncio.run(bounded())


def test_http_lifespan_shutdown_waits_for_active_answer(sample, monkeypatch):
    async def run():
        owner = service(sample)
        closing_entered = asyncio.Event()
        original_close = owner.close

        async def close():
            closing_entered.set()
            await original_close()

        monkeypatch.setattr(owner, 'close', close)
        app = create_app(owner.settings, service_factory=lambda _: owner)
        entered, cancelled, release = hold_retrieval(owner, sample.result)
        watchers = set()
        lifetime = app.router.lifespan_context(app)
        await lifetime.__aenter__()
        task = asyncio.create_task(http_answer(app, asyncio.Queue(), watchers))
        closing = None
        try:
            await entered.wait()
            closing = asyncio.create_task(lifetime.__aexit__(None, None, None))
            await closing_entered.wait()
            assert not closing.done() and owner.busy and not owner.started
            owner.retrieval.close.assert_not_awaited()
            owner.database_close.assert_not_awaited()
            release.set()
            status, body = await task
            assert status == 200 and body['status'] == 'answered'
            await closing
            assert not cancelled.is_set()
            assert watchers and all(w.done() for w in watchers)
            assert not owner.busy and owner._idle.is_set()
            assert not owner.answers.available and owner.answers.catalog is None
            owner.retrieval.close.assert_awaited_once()
            owner.database_close.assert_awaited_once()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            if closing is None:
                await lifetime.__aexit__(None, None, None)
            else:
                await closing

    async def bounded():
        async with asyncio.timeout(5):
            await run()
    asyncio.run(bounded())


@pytest.mark.parametrize('failure', ['missing', 'invalid'])
def test_http_catalog_failure_isolated_from_retrieval_and_health(sample, monkeypatch, failure):
    from src.retrieval.semantics import SemanticSettings

    def load(*args):
        if failure == 'missing':
            raise FileNotFoundError('PRIVATE')
        raise ValueError('PRIVATE')

    monkeypatch.setattr('src.api.answer.load_catalog', load)
    owner = service(sample)
    owner.started = False
    owner.settings = replace(owner.settings, answer=AnswerSettings(enabled=True),
                             semantic=SemanticSettings('expand', Path('fictional')))
    owner.answers.settings = owner.settings
    sample.result['matches'][0].update(cosine_distance=.1, author_id=None, role=None)
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        assert client.get('/health/live').status_code == 200
        assert client.get('/health/ready').status_code == 200
        health = client.get('/health/answer')
        assert health.status_code == 503
        assert health.json() == {'status': 'unavailable', 'reason': 'answer_catalog_unavailable'}
        response = client.post('/api/v1/answer', json={'text': 'Fictional question'})
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'answer_unavailable' and 'PRIVATE' not in response.text
        owner.retrieval.retrieve.assert_not_awaited()
        retrieval = client.post('/api/v1/retrieve', json={'text': 'Fictional question'})
        assert retrieval.status_code == 200
        assert retrieval.json()['matches'][0]['content'] == sample.result['matches'][0]['content']
    owner.retrieval.close.assert_awaited_once()
    owner.database_close.assert_awaited_once()


@pytest.mark.parametrize('mode,expected_reason', [
    ('disabled', 'answer_disabled'),
    ('semantic_off', 'answer_semantics_required'),
    ('identity_mismatch', 'answer_catalog_unavailable'),
    ('ready', None),
])
def test_answer_health_startup_states_and_safe_logs(sample, monkeypatch, caplog, mode, expected_reason):
    from src.retrieval.semantics import SemanticSettings

    caplog.set_level(logging.INFO)
    loader = Mock(return_value=(sample.catalog, sample.lexicon))
    if mode == 'identity_mismatch':
        loader.side_effect = ValueError('answer_catalog_identity_mismatch PRIVATE_SOURCE')
    monkeypatch.setattr('src.api.answer.load_catalog', loader)
    owner = service(sample)
    owner.started = False
    owner.settings = replace(owner.settings, answer=AnswerSettings(enabled=mode != 'disabled'),
                             semantic=SemanticSettings() if mode == 'semantic_off' else
                             SemanticSettings('expand', Path('PRIVATE_PATH')))
    owner.answers.settings = owner.settings
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        response = client.get('/health/answer')
        assert response.status_code == (503 if expected_reason else 200)
        assert response.json() == {'status': 'unavailable' if expected_reason else 'ready',
                                   'reason': expected_reason}
        assert owner.answers.reason == expected_reason
        assert owner.answers.state == ('disabled' if mode == 'disabled' else
                                        'unavailable' if expected_reason else 'ready')
        assert owner.answers.available is (expected_reason is None)
        if mode in {'disabled', 'semantic_off'}:
            loader.assert_not_called()
        else:
            loader.assert_called_once()
        if expected_reason:
            failed = client.post('/api/v1/answer', json={'text': 'PRIVATE_QUESTION'})
            assert failed.status_code == 503
            assert failed.json()['error']['code'] == 'answer_unavailable'
        owner.retrieval.retrieve.assert_not_awaited()
        assert len([r for r in caplog.records if r.message.startswith('answer_startup ')]) == 1
        schema = client.get('/openapi.json').json()['paths']['/health/answer']['get']
        assert {'200', '503'} <= schema['responses'].keys()
    assert owner.answers.state == 'stopped' and not owner.answers.available
    assert owner.answers.catalog is owner.answers.lexicon is None
    assert 'PRIVATE' not in caplog.text + response.text


@pytest.mark.parametrize('failure', ['database', 'model', 'stopped', 'closed'])
def test_answer_health_dependency_failures_do_not_expose_details(sample, failure, caplog):
    owner = service(sample)
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        if failure == 'database':
            owner.database_check.side_effect = RuntimeError('PRIVATE_CONNECTION')
        elif failure == 'model':
            owner.retrieval.started = False
        elif failure == 'stopped':
            owner.started = False
        else:
            client.portal.call(owner.answers.close)
        response = client.get('/health/answer')
        assert response.status_code == 503
        assert response.json() == {'status': 'unavailable', 'reason': 'dependency_unavailable'}
        owner.retrieval.retrieve.assert_not_awaited()
    assert 'PRIVATE' not in caplog.text + response.text


def test_answer_health_remains_available_while_request_owns_busy_slot(sample, monkeypatch):
    from src.retrieval.semantics import SemanticSettings

    loader = Mock(return_value=(sample.catalog, sample.lexicon))
    monkeypatch.setattr('src.api.answer.load_catalog', loader)
    owner = service(sample)
    owner.started = False
    owner.settings = replace(owner.settings, answer=AnswerSettings(enabled=True),
                             semantic=SemanticSettings('expand', Path('fictional')))
    owner.answers.settings = owner.settings
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        owner.busy = True
        owner._idle.clear()
        try:
            for _ in range(2):
                response = client.get('/health/answer')
                assert response.status_code == 200
                assert response.json() == {'status': 'ready', 'reason': None}
            assert owner.busy and not owner._idle.is_set()
            loader.assert_called_once()
            owner.retrieval.retrieve.assert_not_awaited()
        finally:
            owner.busy = False
            owner._idle.set()


def test_answer_processing_failure_has_safe_correlated_log_and_releases_slot(sample, caplog):
    caplog.set_level(logging.INFO)
    owner = service(sample)
    owner.retrieval.retrieve.side_effect = RuntimeError('PRIVATE_EVIDENCE credentials question')
    with TestClient(create_app(owner.settings, service_factory=lambda _: owner)) as client:
        response = client.post('/api/v1/answer', json={'text': 'PRIVATE_QUESTION'})
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'answer_unavailable'
        assert not owner.busy and owner._idle.is_set() and owner.answers.available
    record = next(r for r in caplog.records if r.message.startswith('answer_processing_failed '))
    assert 'exception_type=RuntimeError' in record.message
    request_id = record.message.split('request_id=')[1].split()[0]
    assert len(request_id) == 32 and all(c in '0123456789abcdef' for c in request_id)
    assert f'answer_finished request_id={request_id} status=failed' in caplog.text
    assert record.exc_info is None
    assert 'PRIVATE' not in caplog.text + response.text and 'credentials' not in caplog.text


def test_http_startup_file_enables_answers_despite_inherited_disabled_flag(sample, monkeypatch, tmp_path):
    monkeypatch.setattr('src.api.config.REPOSITORY_ROOT', tmp_path)
    monkeypatch.setenv('ANSWER_ENABLED', 'false')
    monkeypatch.setenv('SEMANTIC_MODE', 'off')
    monkeypatch.setenv('SEMANTIC_ARTIFACT_PATH', 'fictional-process-artifact')
    monkeypatch.setenv('RELEVANCE_FILTER_ENABLED', 'true')
    monkeypatch.delenv('PYTHON_DOTENV_DISABLED', raising=False)
    (tmp_path / '.env').write_text('ANSWER_ENABLED=true\nSEMANTIC_MODE=expand\n'
                                  'SEMANTIC_ARTIFACT_PATH=fictional-file-artifact\n', encoding='utf-8')
    loader = Mock(return_value=(sample.catalog, sample.lexicon))
    monkeypatch.setattr('src.api.answer.load_catalog', loader)
    owners = []

    def factory(settings):
        owner = ApiService(settings, retrieval=SimpleNamespace(start=AsyncMock(), close=AsyncMock(),
                           started=True, retrieve=AsyncMock(return_value=sample.result)),
                           database_check=AsyncMock(), database_close=AsyncMock())
        owners.append(owner)
        return owner

    with TestClient(create_app(service_factory=factory)) as client:
        assert owners[0].settings.answer.enabled
        assert owners[0].settings.semantic.mode == 'expand'
        assert client.get('/health/ready').status_code == 200
        assert client.get('/health/answer').json() == {'status': 'ready', 'reason': None}
        response = client.post('/api/v1/answer', json={'text': 'Sinh con được bao nhiêu?'})
        assert response.status_code == 200 and response.json()['status'] == 'answered'
        loader.assert_called_once()
    owners[0].database_close.assert_awaited_once()


def test_fixed_artifact_defaults_and_explicit_path_overrides(monkeypatch, sample, capsys):
    from src.paths import REPOSITORY_ROOT
    from src.cli import check_answer_catalog as cli
    from src.retrieval.semantic_config import from_environment as semantic_environment
    expected = REPOSITORY_ROOT / 'artifacts/answers/catalog.json'
    assert AnswerSettings().catalog_path == expected
    assert from_environment({}).catalog_path == expected
    custom = 'custom/catalog.json'
    assert from_environment({'ANSWER_CATALOG_PATH': custom}).catalog_path == REPOSITORY_ROOT / custom
    assert semantic_environment({}).artifact_path is None
    calls = []

    def load(catalog, lexicon):
        calls.append((catalog, lexicon))
        return sample.catalog, sample.lexicon

    monkeypatch.setattr(cli, 'load_catalog', load)
    assert cli.main([]) == 0
    assert calls[-1] == (expected, REPOSITORY_ROOT / 'artifacts/semantics/lexicon.json')
    assert cli.main(['--catalog', custom, '--lexicon', 'custom/lexicon.json']) == 0
    assert calls[-1] == (Path(custom), Path('custom/lexicon.json'))
    capsys.readouterr()


def test_missing_current_catalog_does_not_search_versioned_files(tmp_path):
    from src.answering.catalog import load_catalog
    legacy = tmp_path / 'artifacts/answers/v1/catalog.json'
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b'{}')
    with pytest.raises(FileNotFoundError):
        load_catalog(tmp_path / 'artifacts/answers/catalog.json',
                     tmp_path / 'artifacts/semantics/lexicon.json')
