"""Fictional classifier contracts; these tests do not establish real intent quality."""
import asyncio
import copy
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from src.intent import IntentObserver, IntentSettings
from src.intent.config import from_environment, from_arguments
from src.intent.runtime import SOURCES, STYLES, GATES, identity, validate_artifact, scores, classify
from src.ingestion import embeddings
from src.retrieval import RetrievalService, storage
from src.retrieval.reranking import RerankSettings
from src.database.runtime import run_async


def artifact():
    return {'schema_version': 1, 'identity': identity('a' * 40, 'fingerprint', False),
            'source_labels': list(SOURCES), 'style_labels': list(STYLES),
            'source_weights': np.zeros((6, 384)).tolist(), 'source_bias': [8., 0., 0., 0., 0., 0.],
            'style_weights': np.zeros((5, 384)).tolist(), 'style_bias': [8., -8., -8., -8., -8.],
            'source_threshold': .8, 'source_margin': .2, 'style_thresholds': [.5] * 5,
            'acceptance': {'reviewed': True, 'dataset_sha256': 'a' * 64, 'report_sha256': 'b' * 64,
                           'gates': dict.fromkeys(GATES, True)}}


def test_numpy_export_matches_sklearn():
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(42)
    x = rng.normal(size=(90, 384))
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    model = LogisticRegression().fit(x, np.arange(90) % 6)
    binary = LogisticRegression().fit(x, np.arange(90) % 2)
    data = artifact()
    data.update(source_weights=model.coef_.tolist(), source_bias=model.intercept_.tolist(),
                style_weights=np.repeat(binary.coef_, 5, axis=0).tolist(),
                style_bias=np.repeat(binary.intercept_, 5).tolist())
    source, style = scores(validate_artifact(data, data['identity']), x)
    np.testing.assert_allclose(source, model.predict_proba(x), atol=1e-6)
    np.testing.assert_allclose(style[:, 0], binary.predict_proba(x)[:, 1], atol=1e-6)


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(source_weights=[[0.] * 383] * 6),
    lambda d: d['source_bias'].__setitem__(0, float('nan')),
    lambda d: d.update(style_thresholds=[1.1] * 5),
    lambda d: d.update(source_margin=True),
    lambda d: d['identity'].update(files_sha256='changed'),
    lambda d: d['acceptance'].update(reviewed=False),
    lambda d: d['acceptance']['gates'].update(latency=False),
    lambda d: d['acceptance'].update(report_sha256='not-a-hash'),
])
def test_reject_bad_artifact(mutation):
    data = artifact()
    expected = copy.deepcopy(data['identity'])
    mutation(data)
    with pytest.raises(ValueError):
        validate_artifact(data, expected)


def test_classification_and_abstention_are_distinct_from_needs_context():
    data = artifact()
    vector = [1.] + [0.] * 383
    assert classify(data, vector) == {'status': 'classified', 'source': 'type_a', 'styles': ['detail']}
    data['source_bias'] = [0.] * 6
    assert classify(data, vector) == {'status': 'uncertain', 'source': None, 'styles': []}
    data['source_bias'][-1] = 8.
    assert classify(data, vector) == {'status': 'classified', 'source': 'needs_context', 'styles': []}


def test_optional_load_and_safe_logs(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / 'artifact.json'
    path.write_text(json.dumps(artifact()), encoding='utf-8')
    observer = IntentObserver(IntentSettings('observe', path))
    observer.start(artifact()['identity'])
    assert observer.artifact is not None
    observer.observe([1.] + [0.] * 383)
    assert 'source=type_a' in caplog.text
    assert 'source_weights' not in caplog.text
    observer.observe([float('nan')] * 384)
    assert 'inference_failed' in caplog.text
    observer.close()
    assert observer.artifact is None
    path.write_text('{PRIVATE_INVALID_INPUT', encoding='utf-8')
    observer.start(artifact()['identity'])
    assert observer.artifact is None and 'PRIVATE_INVALID_INPUT' not in caplog.text
    off = IntentObserver(IntentSettings('off', path))
    off.start(artifact()['identity'])
    assert off.artifact is None


def test_config_defaults_and_invalid_mode():
    assert from_environment({}) == IntentSettings()
    assert from_arguments(SimpleNamespace()) == IntentSettings()
    assert from_environment({'INTENT_MODE': 'observe'}).mode == 'observe'
    with pytest.raises(ValueError):
        from_environment({'INTENT_MODE': 'route'})


@pytest.mark.parametrize('rerank', [False, True])
@pytest.mark.parametrize('calibration', ['missing', 'accepted', 'stale'])
def test_observation_preserves_results_and_encode_count(monkeypatch, tmp_path, caplog, rerank, calibration):
    caplog.set_level(logging.INFO)
    from src.retrieval.reranking import OnnxReranker
    model = SimpleNamespace(max_seq_length=512, tokenizer=Mock(return_value={'input_ids': [1, 2]}),
                            encode=Mock(return_value=np.array([[1.] + [0.] * 383])))
    model.tokenizer.model_max_length = 512
    monkeypatch.setattr(embeddings, 'load_model', lambda *a, **kw: model)
    monkeypatch.setattr(embeddings, 'model_fingerprint', lambda *a: 'fingerprint')
    base = {'status': 'matches_found', 'corpus_fingerprint': 'corpus', 'matches': [
        {'chunk_id': 'fictional', 'chunk_index': 0, 'revision_id': 'rev', 'run_id': 'run',
         'content': 'Fictional evidence', 'cosine_distance': .1}]}
    search = AsyncMock(side_effect=lambda *a, **kw: copy.deepcopy(base))
    monkeypatch.setattr(storage, 'search', search)
    def start(r):
        r.fingerprint = 'reranker'
        r.profile = None if calibration == 'missing' else {'holdout_passed': True, 'threshold': 0.,
            'fingerprint': 'reranker', 'corpus_fingerprint': 'corpus' if calibration == 'accepted' else 'old'}
    monkeypatch.setattr(OnnxReranker, 'start', start)
    monkeypatch.setattr(OnnxReranker, 'score', lambda *a: ([1.], 1))
    data = artifact()
    data['identity'] = identity('a' * 40, 'fingerprint', rerank)
    path = tmp_path / 'artifact.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    async def scenario():
        results = []
        for mode in ('off', 'observe'):
            s = RetrievalService(model_path='fake', model_revision='a' * 40,
                                 rerank=RerankSettings(enabled=rerank), intent=IntentSettings(mode, path))
            await s.start()
            result = await s.retrieve('thai sảng')
            result.pop('rerank_seconds')
            results.append(result)
            await s.close()
            assert s._intent.artifact is None
        assert results[0] == results[1]
    run_async(scenario())
    assert model.encode.call_count == (4 if rerank else 2)
    assert search.await_count == 2
    assert 'source=type_a' in caplog.text
    assert 'thai sảng' not in caplog.text


def test_observation_cancellation_drains_worker(monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    async def scenario():
        service = RetrievalService(model_path='fake', model_revision='a' * 40,
                                   intent=IntentSettings('observe'))
        service._model = object()
        monkeypatch.setattr(service, '_encode', lambda q: [1.] + [0.] * 383)
        def observe(vector):
            entered.set()
            release.wait(5)
        monkeypatch.setattr(service._intent, 'observe', observe)
        task = asyncio.create_task(service.retrieve('fictional'))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(.01)
            task.cancel()
            closing = asyncio.create_task(service.close())
            await asyncio.sleep(.01)
            assert not closing.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await closing
    run_async(scenario())


def test_http_observe_wiring_keeps_response_contract(tmp_path, monkeypatch, caplog):
    from fastapi.testclient import TestClient
    from src.api.app import create_app
    from src.api.config import Settings
    from src.api.service import ApiService
    caplog.set_level(logging.INFO)
    model = SimpleNamespace(max_seq_length=512, tokenizer=Mock(return_value={'input_ids': [1, 2]}),
                            encode=Mock(return_value=np.array([[1.] + [0.] * 383])))
    model.tokenizer.model_max_length = 512
    monkeypatch.setattr(embeddings, 'load_model', lambda *a, **kw: model)
    monkeypatch.setattr(embeddings, 'model_fingerprint', lambda *a: 'fingerprint')
    monkeypatch.setattr(storage, 'search', AsyncMock(return_value={'status': 'no_eligible_documents', 'matches': []}))
    path = tmp_path / 'artifact.json'
    path.write_text(json.dumps(artifact()), encoding='utf-8')
    settings = Settings(model_path=tmp_path, model_revision='a' * 40, intent=IntentSettings('observe', path))
    service = ApiService(settings, database_check=AsyncMock(), database_close=AsyncMock())
    with TestClient(create_app(settings, service_factory=lambda _: service)) as client:
        response = client.post('/api/v1/retrieve', json={'text': 'Fictional private question'})
        assert response.status_code == 200
        assert set(response.json()) == {'status', 'matches', 'fallback_reason', 'relevance', 'timings'}
        assert response.json()['fallback_reason'] == 'no_eligible_documents'
    assert model.encode.call_count == 1
    assert 'source=type_a' in caplog.text and 'Fictional private question' not in caplog.text


def test_cli_observe_emits_safe_stderr_without_changing_json(monkeypatch, capsys):
    from src.cli import retrieve_documents as cli
    async def run(args):
        assert args.intent_mode == 'observe'
        logging.getLogger('src.intent.runtime').info('intent_observed status=unavailable')
        print('MODEL_OUTPUT_MUST_BE_SILENCED')
        return {'status': 'no_matches', 'matches': []}, 0
    monkeypatch.setattr(cli, 'run', run)
    logger = logging.getLogger('src.intent.runtime')
    original = (logger.level, logger.propagate, list(logger.handlers))
    assert cli.main(['--question', 'private question', '--model-path', 'unused', '--model-revision', 'a' * 40,
                     '--intent-mode', 'observe', '--json']) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {'status': 'no_matches', 'matches': []}
    assert captured.err.strip() == 'intent_observed status=unavailable'
    assert (logger.level, logger.propagate, list(logger.handlers)) == original
