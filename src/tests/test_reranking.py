"""Synthetic online ranking and lifecycle regressions, without hosted inference."""
import asyncio
import math
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from src.database.runtime import run_async
from src.ingestion import embeddings
from src.retrieval import RetrievalService, RetrievalError, storage
from src.retrieval.config import from_environment
from src.retrieval.reranking import OnnxReranker, RerankSettings, query_variants, fuse_candidates, relevance


def test_normalization_and_bounded_corrections():
    import unicodedata
    assert query_variants(unicodedata.normalize('NFD', '  thai sảng\n  ')) == ['thai sảng', 'thai sản']
    assert query_variants('NGÀY TẾT NGUYÊN ĐÁNG 2027') == ['NGÀY TẾT NGUYÊN ĐÁNG 2027', 'NGÀY TẾT nguyên đán 2027']
    assert query_variants('xthai sảngz 1.000.000 Nguyễn An') == ['xthai sảngz 1.000.000 Nguyễn An']


def test_rrf_deduplicates_and_preserves_original_distance():
    a = {'chunk_id': 'a', 'cosine_distance': .7}
    b = {'chunk_id': 'b', 'cosine_distance': .2}
    assert fuse_candidates([[a, b], [b, a]], 2) == [a, b]
    assert fuse_candidates([[a, a], [b]], 2) == [a, b]


class Tokenizer:
    def encode(self, text, **kwargs):
        return list(range(10, 10 + int(text)))

    def num_special_tokens_to_add(self, **kwargs):
        return 4

    def pad(self, batch, **kwargs):
        length = max(len(row['input_ids']) for row in batch)
        return {key: np.array([row[key] + [0] * (length - len(row[key])) for row in batch])
                for key in ('input_ids', 'attention_mask')}


def test_windows_preserve_tail_query_and_bound_batch():
    r = OnnxReranker(RerankSettings(enabled=True, batch_size=2))
    r.tokenizer = Tokenizer()
    batches = []
    def infer(_, inputs):
        batches.append(inputs)
        return [inputs['input_ids'].max(axis=1).reshape(-1, 1)]
    r.session = SimpleNamespace(get_inputs=lambda: [SimpleNamespace(name=n) for n in ('input_ids', 'attention_mask')], run=infer)
    scores, windows = r.score('10', [{'content': '1300'}, {'content': '2'}])
    assert windows == 4 and scores == [1309., 19.]
    for batch in batches:
        assert batch['input_ids'].shape[0] <= 2 and batch['input_ids'].shape[1] <= 512
        assert list(batch['input_ids'][0][:13]) == [0] + list(range(10, 20)) + [2, 2]
    with pytest.raises(ValueError, match='question_exceeds_model_limit'):
        r.score('500', [{'content': '1'}])
    r.session.run = lambda *_: [np.array([[math.nan]])]
    with pytest.raises(ValueError, match='invalid_reranker_scores'):
        r.score('1', [{'content': '1'}])


@pytest.mark.parametrize('values', [{'RERANK_ENABLED': 'yes'}, {'RERANK_CANDIDATE_COUNT': '9'},
    {'RERANK_BATCH_SIZE': '0'}, {'RERANK_MODEL_FILE': '../other.onnx'}])
def test_config_rejects_invalid_settings(values):
    with pytest.raises(ValueError):
        from_environment(values)


def test_relevance_keeps_low_candidates_and_never_certifies_completeness():
    matches = [{'rerank_score': -4.}]
    assert relevance(matches)['status'] == 'unassessed'
    assert relevance([])['status'] == 'no_candidates'
    assert relevance(matches)['warnings'] == ['evidence_completeness_not_assessed', 'relevance_not_assessed']


def install_doubles(monkeypatch):
    tokenizer = Mock(return_value={'input_ids': [1]})
    tokenizer.model_max_length = 512
    model = SimpleNamespace(max_seq_length=512, tokenizer=tokenizer,
        encode=lambda *a, **k: [SimpleNamespace(tolist=lambda: [1.] + [0.] * 383)])
    monkeypatch.setattr(embeddings, 'load_model', lambda *a, **k: model)
    monkeypatch.setattr(embeddings, 'model_fingerprint', lambda *_: 'synthetic')
    monkeypatch.setattr(OnnxReranker, 'start', lambda self: setattr(self, 'fingerprint', 'synthetic'))
    matches = [dict(chunk_id=str(i), chunk_index=i, revision_id='rev', run_id='run',
                    content='synthetic', cosine_distance=i / 10) for i in range(3)]
    search = AsyncMock(return_value={'status': 'matches_found', 'matches': matches, 'corpus_fingerprint': 'corpus'})
    monkeypatch.setattr(storage, 'search', search)
    return search


def test_online_orchestration_and_fail_closed(monkeypatch):
    search = install_doubles(monkeypatch)
    monkeypatch.setattr(OnnxReranker, 'score', lambda *args: ([0., 9., 9.], 3))
    async def scenario():
        service = RetrievalService(model_path='unused', model_revision='a'*40, rerank=RerankSettings(enabled=True))
        await service.start()
        result = await service.retrieve('thai sảng', 2)
        assert [m['chunk_id'] for m in result['matches']] == ['1', '2']
        assert 'candidate_order' not in result
        assert result['relevance']['status'] == 'unassessed'
        assert search.call_args.args[3] == 30 and len(search.call_args.kwargs['extra_vectors']) == 1
        monkeypatch.setattr(OnnxReranker, 'score', Mock(side_effect=RuntimeError('private text')))
        with pytest.raises(RetrievalError, match='reranking_failed'):
            await service.retrieve('question', 2)
        await service.close()
    run_async(scenario())


def test_cancelled_rerank_drains_before_close(monkeypatch):
    install_doubles(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    def score(*args):
        entered.set()
        assert release.wait(5)
        return [0., 1., 2.], 3
    monkeypatch.setattr(OnnxReranker, 'score', score)
    async def scenario():
        service = RetrievalService(model_path='unused', model_revision='a'*40, rerank=RerankSettings(enabled=True))
        await service.start()
        task = asyncio.create_task(service.retrieve('question'))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        close = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        assert not close.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await close
    run_async(scenario())


def test_start_failure_clears_readiness_and_never_falls_back(monkeypatch):
    install_doubles(monkeypatch)
    monkeypatch.setattr(OnnxReranker, 'start', Mock(side_effect=ValueError('private path')))
    async def scenario():
        s = RetrievalService(model_path='unused', model_revision='a'*40, rerank=RerankSettings(enabled=True))
        with pytest.raises(RetrievalError, match='model_load_failed'):
            await s.start()
        assert not s.started
        with pytest.raises(RetrievalError, match='service_not_started'):
            await s.retrieve('question')
        await s.close()
    run_async(scenario())


@pytest.mark.parametrize('status,reason', [('no_eligible_documents', 'no_eligible_documents'),
                                         ('no_matches', 'no_candidates'),
                                         ('matches_found', None)])
def test_public_pipeline_distinguishes_empty_outcomes_without_leaking_candidates(monkeypatch, status, reason):
    import json
    search = install_doubles(monkeypatch)
    if status != 'matches_found':
        search.return_value.update(status=status, matches=[])
    else:
        for match in search.return_value['matches']:
            match['content'] = 'WITHHELD FICTIONAL PASSAGE'
    monkeypatch.setattr(OnnxReranker, 'score', lambda *a: ([-3., -2., -1.], 3))

    async def scenario():
        service = RetrievalService(model_path='unused', model_revision='a' * 40,
                                   rerank=RerankSettings(enabled=True))
        await service.start()
        try:
            result = await service.retrieve('Fictional policy?', 5)
            assert result['status'] == status
            assert result['fallback_reason'] == reason
            assert bool(result['matches']) == (status == 'matches_found')
            assert 'candidate_order' not in result
            assert ('WITHHELD FICTIONAL PASSAGE' in json.dumps(result)) == (status == 'matches_found')
        finally:
            await service.close()
    run_async(scenario())


@pytest.mark.parametrize('status,reason', [('no_eligible_documents', 'no_eligible_documents'),
                                        ('no_matches', 'no_candidates')])
def test_unassessed_retrieval_preserves_empty_outcomes(monkeypatch, status, reason):
    search = install_doubles(monkeypatch)
    search.return_value = dict(status=status, matches=[])
    async def scenario():
        service = RetrievalService(model_path='unused', model_revision='a'*40)
        await service.start()
        try:
            result = await service.retrieve('question')
            assert result['status'] == status and result['fallback_reason'] == reason
            assert result['matches'] == []
            assert result['relevance'] == dict(status='no_candidates', warnings=[
                'evidence_completeness_not_assessed', 'relevance_not_assessed'])
            assert 'candidate_order' not in result
        finally:
            await service.close()
    run_async(scenario())


def test_unassessed_retrieval_does_not_hide_reranker_failure(monkeypatch):
    install_doubles(monkeypatch)
    monkeypatch.setattr(OnnxReranker, 'score', Mock(side_effect=RuntimeError('private')))
    async def scenario():
        service = RetrievalService(model_path='unused', model_revision='a'*40,
            rerank=RerankSettings(enabled=True))
        await service.start()
        try:
            with pytest.raises(RetrievalError, match='reranking_failed'):
                await service.retrieve('question')
        finally:
            await service.close()
    run_async(scenario())
