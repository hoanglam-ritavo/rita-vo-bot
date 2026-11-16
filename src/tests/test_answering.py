"""Grounded answer/API regressions with fictional evidence and no model/database."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from src.answering.acceptance import CATEGORIES, configuration, load_acceptance
from src.answering.config import AnswerSettings
from src.answering.gguf import model_metadata
from src.answering.identity import implementation_identity, retrieval_identity, sha_file
from src.answering.pipeline import evaluate_question, evidence_sources
from src.api.app import create_app
from src.api.config import Settings
from src.api.contracts import ApiError
from src.api.service import ApiService
import src.api.answer as answer_module


def match(**changes):
    return dict(content='Fictional policy: permanent staff receive 100 credits, except temporary staff.',
        title='Fictional policy', applicability='Permanent staff', effective_from=None, effective_to=None,
        version_label='v1', chunk_id='c1', document_id='d1', revision_id='r1', run_id='run1',
        source_path='fictional.pdf', source_spans=[{'location': {'kind': 'pdf', 'page_start': 1, 'page_end': 1}}],
        chunk_index=0, cosine_distance=.1, author_id=None, role='detail') | changes


def evidence(**changes):
    return dict(status='matches_found', matches=[match()], fallback_reason=None,
        corpus_fingerprint='fictional-corpus',
        relevance={'status': 'relevant_candidates', 'warnings': ['evidence_completeness_not_assessed']}) | changes


def draft(**changes):
    return dict(status='answered', claims=[{'text': 'Nhân viên chính thức được 100 credits; loại trừ nhân viên tạm thời.',
        'citations': [{'source_id': 'S1', 'quote': match()['content']}]}], limitations=[]) | changes


def verdict(**changes):
    return dict(checks=[{'claim_index': 0, 'supported': True, 'conditions_preserved': True}],
        status_appropriate=True, missing_parts_disclosed=True, no_unresolved_conflict=True) | changes


def owner():
    service = ApiService(Settings(Path('unused'), 'a' * 40), retrieval=AsyncMock(),
                         database_check=AsyncMock(), database_close=AsyncMock())
    service.retrieval.started = True
    service.retrieval.retrieve.return_value = evidence()
    return service


def enable(service, runtime=None):
    service.started = True
    service.answers.available = True
    service.answers.corpus = 'fictional-corpus'
    service.answers.runtime = runtime or SimpleNamespace(
        complete=AsyncMock(side_effect=[(draft(), {}), (verdict(), {})]), close=AsyncMock())


def test_dedup_preserves_origins_and_different_conditions():
    sources = evidence_sources([match(), match(chunk_id='c2', source_path='copy.pdf'),
                               match(applicability='All staff'), match(version_label='v2')])
    assert len(sources) == 3
    assert [o['chunk_id'] for o in sources['S1']['origins']] == ['c1', 'c2']
    assert sources['S2']['applicability'] == 'All staff'
    assert sources['S3']['version_label'] == 'v2'


@pytest.mark.parametrize('change', [
    {'relevance': {'status': 'unassessed', 'warnings': []}},
    {'relevance': {'status': 'relevant_candidates', 'warnings': ['relevance_filter_disabled']}},
    {'relevance': {}}, {'corpus_fingerprint': 'changed'}])
def test_unassessed_or_changed_corpus_never_reaches_model(change):
    async def run():
        retrieval = SimpleNamespace(retrieve=AsyncMock(return_value=evidence(**change)))
        runtime = SimpleNamespace(complete=AsyncMock())
        with pytest.raises(ValueError):
            await evaluate_question('Question', retrieval, runtime, expected_corpus='fictional-corpus')
        runtime.complete.assert_not_awaited()
    asyncio.run(run())


def test_disabled_api_does_not_start_model_and_retrieval_works():
    service = owner()
    with TestClient(create_app(service.settings, service_factory=lambda _: service)) as client:
        assert client.post('/api/v1/answer', json={'text': 'Question'}).status_code == 503
        assert client.post('/api/v1/retrieve', json={'text': 'Question'}).status_code == 200
    service.retrieval.retrieve.assert_awaited_once()


@pytest.mark.parametrize('top_k', [None, 3])
def test_http_answer_serialized_sources_and_request_defaults(top_k):
    service = owner()
    service.settings = replace(service.settings, top_k=7)
    with TestClient(create_app(service.settings, service_factory=lambda _: service)) as client:
        enable(service)
        body = {'text': '  Fictional question?  '}
        if top_k is not None:
            body['top_k'] = top_k
        response = client.post('/api/v1/answer', json=body)
        assert response.status_code == 200
        body = response.json()
        assert body['status'] == 'answered'
        assert body['claims'][0]['text'] in body['answer'] and '[S1]' in body['answer']
        assert body['sources'][0]['source_id'] == 'S1'
        assert body['sources'][0]['origins'][0]['source_path'] == 'fictional.pdf'
        assert 'content' not in body['sources'][0]
        assert not {'draft', 'verdict', 'confidence', 'reasoning'} & body.keys()
        service.retrieval.retrieve.assert_awaited_once_with('Fictional question?', top_k=top_k or 5)
        assert 'timings' in body


@pytest.mark.parametrize('body', [{'text': ''}, {'text': 'Question', 'top_k': 21},
                                  {'text': 'Question', 'conversation': []}])
def test_answer_input_validation(body):
    service = owner()
    with TestClient(create_app(service.settings, service_factory=lambda _: service)) as client:
        assert client.post('/api/v1/answer', json=body).status_code == 422
    service.retrieval.retrieve.assert_not_awaited()


@pytest.mark.parametrize('status', ['partial', 'needs_clarification', 'unsupported'])
def test_partial_and_clarification_keep_status_and_limitations(status):
    async def run():
        service = owner()
        answering = status == 'partial'
        value = draft(status=status, claims=draft()['claims'] if answering else [],
                      limitations=['Bạn muốn hỏi về loại chính sách nào?'])
        check = verdict(checks=verdict()['checks'] if answering else [])
        enable(service, SimpleNamespace(complete=AsyncMock(side_effect=[(value, {}), (check, {})]), close=AsyncMock()))
        response = await service.answers.answer('Question', 5)
        assert response.status == status and value['limitations'][0] in response.answer
        assert bool(response.sources) == answering
        assert service.answers.runtime.complete.await_count == 2
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['invalid_citation', 'bad_verdict', 'bad_json_shape'])
def test_rejected_drafts_never_escape_response_or_logs(failure, caplog):
    async def run():
        service = owner()
        value, check = draft(), verdict()
        if failure == 'invalid_citation':
            value['claims'][0]['citations'][0]['quote'] = 'PRIVATE invented claim'
        elif failure == 'bad_verdict':
            check['checks'][0]['conditions_preserved'] = False
        else:
            value = {'secret_draft': 'PRIVATE'}
        enable(service, SimpleNamespace(complete=AsyncMock(side_effect=[(value, {}), (check, {})]), close=AsyncMock()))
        response = await service.answers.answer('PRIVATE question', 5)
        assert response.status == 'insufficient_evidence'
        assert response.claims == response.sources == []
        assert 'PRIVATE' not in response.model_dump_json() + caplog.text
        assert service.answers.available  # Validation rejection does not imply runtime failure.
    asyncio.run(run())


def test_no_matches_skips_generation():
    async def run():
        service = owner()
        enable(service)
        service.retrieval.retrieve.return_value = evidence(status='no_matches', matches=[],
            fallback_reason='below_relevance_threshold', relevance={'status': 'low_relevance', 'warnings': []})
        result = await service.answers.answer('Question', 5)
        assert result.status == 'insufficient_evidence' and result.fallback_reason == 'below_relevance_threshold'
        service.answers.runtime.complete.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['timeout', 'inference', 'cancel'])
def test_failed_request_closes_runtime_and_releases_owner(failure, monkeypatch):
    async def run():
        service = owner()
        entered = asyncio.Event()
        async def complete(*args):
            entered.set()
            if failure == 'inference':
                await asyncio.sleep(.01)
                raise RuntimeError('PRIVATE model text')
            await asyncio.Event().wait()
        enable(service, SimpleNamespace(complete=complete, close=AsyncMock()))
        monkeypatch.setattr(answer_module, 'DEADLINE_SECONDS', .02 if failure == 'timeout' else 30)
        task = asyncio.create_task(service.answers.answer('Question', 5))
        await entered.wait()
        with pytest.raises(ApiError) as busy:
            await service.retrieve('Other question')
        assert busy.value.status_code == 429
        if failure == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ApiError) as caught:
                await task
            assert caught.value.status_code == (504 if failure == 'timeout' else 503)
        service.answers.runtime.close.assert_awaited_once()
        assert not service.busy and not service.answers.available and service._idle.is_set()
        assert (await service.retrieve('Question')).status == 'matches_found'
    asyncio.run(run())


def test_repeated_cancellation_keeps_busy_until_runtime_stops():
    async def run():
        service = owner()
        generating, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def complete(*args):
            generating.set()
            await asyncio.Event().wait()
        async def close():
            closing.set()
            await release.wait()
        enable(service, SimpleNamespace(complete=complete, close=close))
        task = asyncio.create_task(service.answers.answer('Question', 5))
        await generating.wait()
        task.cancel()
        await closing.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert service.busy and not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not service.busy
    asyncio.run(run())


def reviewed_files(tmp_path, settings):
    rows = []
    for split in ('development', 'holdout'):
        for category, count in CATEGORIES.items():
            for index in range(count // 2):
                rows.append(dict(id=f'{split}-{category}-{index}', split=split, category=category,
                    critical=category == 'adversarial', accepted=True, status='answered', error=None, seconds=10.))
    report = dict(schema_version=3, status='awaiting_manual_review', error=None, cases=rows,
        runtime_identity={'model': 'm', 'runtime': 'r'}, implementation_sha256=implementation_identity(),
        retrieval_identity=retrieval_identity(settings), configuration=configuration(),
        corpus_fingerprint='fictional-corpus', peak_system_memory_percent=70, runtime_measured=True)
    reviews = dict(user_accepted=True, cases=[dict(id=r['id'], correct_and_complete=True,
        no_unsupported_claims=True, sources_valid=True, critical_passed=True, more_useful_than_excerpts=True) for r in rows])
    samples = tmp_path / 'samples.private.json'
    samples.write_text('[]', encoding='utf-8')
    report['samples_sha256'] = sha_file(samples)
    report_path, review_path = tmp_path / 'report.json', tmp_path / 'review.json'
    def save():
        report_path.write_text(json.dumps(report), encoding='utf-8')
        reviews['report_sha256'] = sha_file(report_path)
        review_path.write_text(json.dumps(reviews), encoding='utf-8')
    save()
    return report_path, review_path, report, reviews, save


@pytest.mark.parametrize('change', ['none', 'unaccepted', 'model', 'code', 'pipeline', 'configuration', 'slow', 'samples', 'review_hash', 'old_report'])
def test_acceptance_requires_matching_manual_review_and_runtime(tmp_path, change):
    settings = owner().settings
    report_path, review_path, report, review, save = reviewed_files(tmp_path, settings)
    if change == 'unaccepted':
        review['user_accepted'] = False
    elif change in ('model', 'code', 'pipeline', 'configuration', 'old_report'):
        field = {'model': 'runtime_identity', 'code': 'implementation_sha256', 'pipeline': 'retrieval_identity',
                 'configuration': 'configuration', 'old_report': 'schema_version'}[change]
        report[field] = 'changed'
    elif change == 'slow':
        for row in report['cases'][:4]:
            row['seconds'] = 31
    save()
    if change == 'samples':
        (tmp_path / 'samples.private.json').write_text('[1]', encoding='utf-8')
    elif change == 'review_hash':
        report_path.write_text('{}', encoding='utf-8')
    if change == 'none':
        assert load_acceptance(report_path, review_path, settings, {'model': 'm', 'runtime': 'r'}) == 'fictional-corpus'
    else:
        with pytest.raises(ValueError):
            load_acceptance(report_path, review_path, settings, {'model': 'm', 'runtime': 'r'})


def test_missing_acceptance_leaves_retrieval_available():
    service = owner()
    service.settings = replace(service.settings, answer=AnswerSettings(enabled=True))
    service.answers.settings = service.settings
    with TestClient(create_app(service.settings, service_factory=lambda _: service)) as client:
        assert client.post('/api/v1/answer', json={'text': 'Question'}).status_code == 503
        assert client.post('/api/v1/retrieve', json={'text': 'Question'}).status_code == 200


@pytest.mark.parametrize('warmup_failure', [False, True])
def test_enabled_lifecycle_requires_review_then_warmup(monkeypatch, warmup_failure):
    async def run():
        from src.retrieval.reranking import RerankSettings
        service = owner()
        service.settings = replace(service.settings, rerank=RerankSettings(enabled=True),
            answer=AnswerSettings(enabled=True, report_path=Path('report'),
                                  review_path=Path('review')))
        service.answers.settings = service.settings
        runtime = SimpleNamespace(verify_files=lambda: {'runtime': 'r', 'model': 'm'},
            start=AsyncMock(), warmup=AsyncMock(side_effect=ValueError('PRIVATE') if warmup_failure else None),
            close=AsyncMock())
        monkeypatch.setattr(answer_module, 'LocalRuntime', lambda *a, **kw: runtime)
        monkeypatch.setattr(answer_module, 'load_acceptance', lambda *a: 'fictional-corpus')
        await service.start()
        assert service.started and service.answers.available is not warmup_failure
        runtime.start.assert_awaited_once()
        runtime.warmup.assert_awaited_once()
        await service.close()
        service.retrieval.close.assert_awaited_once()
        service.database_close.assert_awaited_once()
    asyncio.run(run())


def test_http_disconnect_cancels_inference_and_drains_owned_runtime():
    async def run():
        service = owner()
        entered = asyncio.Event()
        async def complete(*args):
            entered.set()
            await asyncio.Event().wait()
        enable(service, SimpleNamespace(complete=complete, close=AsyncMock()))
        app = create_app(service.settings)
        app.state.retrieval_service = service
        first = True
        async def receive():
            nonlocal first
            if first:
                first = False
                return {'type': 'http.request', 'body': b'{"text":"Question"}', 'more_body': False}
            await entered.wait()
            return {'type': 'http.disconnect'}
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
            'method': 'POST', 'scheme': 'http', 'path': '/api/v1/answer', 'raw_path': b'/api/v1/answer',
            'query_string': b'', 'headers': [(b'content-type', b'application/json')],
            'client': ('127.0.0.1', 1234), 'server': ('127.0.0.1', 8000), 'root_path': ''}
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(app(scope, receive, AsyncMock()), timeout=2)
        service.answers.runtime.close.assert_awaited_once()
        assert not service.answers.available and not service.busy
    asyncio.run(run())


# Native thinking/context/grammar regressions live in test_native_answer_runtime.py.


def test_gguf_inspection_rejects_wrong_model(tmp_path):
    path = tmp_path / 'fictional.gguf'
    path.write_bytes(b'not gguf')
    with pytest.raises(ValueError):
        model_metadata(path)
    path.write_bytes(struct.pack('<4sIQQ', b'GGUF', 3, 0, 0))
    with pytest.raises(ValueError, match='unexpected_answer_model'):
        model_metadata(path)


@pytest.mark.parametrize('blocks,nextn,quant,valid', [(24, 0, 17, True), (25, 1, 17, True),
    (24, 0, 15, False), (25, 0, 17, False), (25, 2, 17, False)])
def test_q5_metadata_accepts_base_layers_with_optional_prediction_layer(tmp_path, blocks, nextn, quant, valid):
    path = tmp_path / 'model.gguf'
    values = {'general.architecture': 'qwen35', 'general.size_label': '0.8B',
              'qwen35.block_count': blocks, 'qwen35.embedding_length': 1024,
              'qwen35.nextn_predict_layers': nextn, 'general.file_type': quant}
    def string(value):
        encoded = value.encode('utf-8')
        return struct.pack('<Q', len(encoded)) + encoded
    content = struct.pack('<4sIQQ', b'GGUF', 3, 0, len(values))
    for key, value in values.items():
        content += string(key)
        content += struct.pack('<I', 8 if isinstance(value, str) else 4)
        content += string(value) if isinstance(value, str) else struct.pack('<I', value)
    path.write_bytes(content)
    if valid:
        assert model_metadata(path)['general.file_type'] == 17
    else:
        with pytest.raises(ValueError, match='unexpected_answer_model'):
            model_metadata(path)
