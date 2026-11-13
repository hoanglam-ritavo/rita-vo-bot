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
    assert relevance(matches, 0)['status'] == 'low_relevance'
    assert relevance(matches, -4)['status'] == 'relevant_candidates'
    assert relevance([], 0)['status'] == 'no_candidates'
    assert relevance(matches, -4)['warnings'] == ['evidence_completeness_not_assessed']


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
        service._reranker.profile = dict(fingerprint='synthetic', corpus_fingerprint='corpus', threshold=9., holdout_passed=True)
        result = await service.retrieve('thai sảng', 2)
        assert [m['chunk_id'] for m in result['matches']] == ['1', '2']
        assert 'candidate_order' not in result
        assert result['relevance']['status'] == 'relevant_candidates'
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


def test_calibration_checks_every_returned_passage_not_just_top_one():
    from scripts.benchmark_online_retrieval import calibrate
    def row(split, supported):
        return dict(split=split, relevant=supported, _revision='rev', _run='run',
                    _case=dict(id=split+str(supported), category='detail' if supported else 'unsupported',
                               required_groups=[[0]] if supported else []),
                    after_rerank=[dict(chunk_index=i, revision_id='rev', run_id='run',
                                      rerank_score=score, relevant=supported and i == 0)
                                  for i, score in enumerate([3., -2.] if supported else [-2.])])
    rows = [row(split, supported) for split in ('calibration', 'holdout') for supported in (True, False)]
    assert calibrate(rows)['holdout_passed']
    rows[2]['after_rerank'][1]['rerank_score'] = 2.5
    assert not calibrate(rows)['holdout_passed']
    assert not calibrate(rows[:2])['holdout_passed']


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


def test_profile_requires_matching_identity_corpus_and_validated_holdout(tmp_path):
    import json
    from src.retrieval.reranking import profile_threshold, load_profile
    path = tmp_path / 'profile.json'
    assert load_profile(path) is None
    good = dict(schema_version=2, fingerprint='current', corpus_fingerprint='corpus', threshold=-3.2, holdout_passed=True)
    path.write_text(json.dumps(good), encoding='utf-8')
    profile = load_profile(path)
    assert profile_threshold(profile, 'current', 'corpus') == -3.2
    assert profile_threshold(profile, 'stale', 'corpus') is None
    assert profile_threshold(profile, 'current', 'changed') is None
    assert profile_threshold(profile, 'current', None) is None
    for changes in ({'threshold': True}, {'threshold': math.nan}, {'corpus_fingerprint': None}):
        path.write_text(json.dumps(good | changes), encoding='utf-8')
        with pytest.raises(ValueError, match='invalid_relevance_profile'):
            load_profile(path)
    path.write_text(json.dumps({k: v for k, v in good.items() if k != 'schema_version'}))
    assert load_profile(path) is None


def test_benchmark_rejects_writing_into_model_or_case_inputs(tmp_path, monkeypatch):
    import json
    from scripts import benchmark_online_retrieval as bench
    from scripts import evaluate_retrieval as evaluation
    monkeypatch.setattr(evaluation, 'REPOSITORY_ROOT', tmp_path)
    root = tmp_path / '.tmp'
    root.mkdir()
    model = root / 'model'
    model.mkdir()
    manifest = root / 'manifest.json'
    case = root / 'cases.json'
    manifest.write_text(json.dumps({'datasets': [{'package': str(root / 'package'), 'cases': str(case)}]}))
    args = SimpleNamespace(manifest=manifest, model_path=model, rerank_model_path=model,
                           requests=100, output=model / 'new.json')
    with pytest.raises(ValueError, match='output_overwrites_input'):
        bench.validate_outputs(args)
    args.output = case
    with pytest.raises(ValueError, match='output_overwrites_input'):
        bench.validate_outputs(args)
    args.output = root / 'report.json'
    bench.validate_outputs(args)


@pytest.mark.parametrize('threshold,expected,reason', [(2., ['2'], None), (1., ['1', '2'], None),
    (3., [], 'below_relevance_threshold'), (None, [], 'relevance_unavailable')])
def test_filter_never_fills_top_k(threshold, expected, reason):
    from src.retrieval.reranking import filter_candidates
    matches = [dict(chunk_id=str(i), rerank_score=float(i), source_spans=['fictional']) for i in range(3)]
    result = filter_candidates(dict(status='matches_found', matches=matches), threshold, 5)
    assert [m['chunk_id'] for m in result['matches']] == expected
    assert result['fallback_reason'] == reason
    assert result['status'] == ('matches_found' if expected else 'no_matches')
    assert all(m['source_spans'] == ['fictional'] for m in result['matches'])


@pytest.mark.parametrize('enabled,corpus', [(False, 'corpus'), (True, 'changed'), (True, None)])
def test_unavailable_assessment_never_exposes_candidates(monkeypatch, enabled, corpus):
    search = install_doubles(monkeypatch)
    search.return_value['corpus_fingerprint'] = corpus
    monkeypatch.setattr(OnnxReranker, 'score', lambda *args: ([1., 2., 3.], 3))
    async def scenario():
        service = RetrievalService(model_path='unused', model_revision='a'*40, rerank=RerankSettings(enabled=enabled))
        await service.start()
        try:
            if enabled:
                service._reranker.profile = dict(fingerprint='synthetic', corpus_fingerprint='corpus', threshold=0, holdout_passed=True)
            result = await service.retrieve('question', 5)
            assert result['matches'] == [] and result['fallback_reason'] == 'relevance_unavailable'
            assert 'candidate_order' not in result
        finally:
            await service.close()
    run_async(scenario())


@pytest.mark.parametrize('status,reason', [('no_eligible_documents', 'no_eligible_documents'),
                                         ('no_matches', 'no_candidates'),
                                         ('matches_found', 'below_relevance_threshold')])
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
            service._reranker.profile = dict(fingerprint='synthetic', corpus_fingerprint='corpus',
                                            threshold=0., holdout_passed=True)
            result = await service.retrieve('Fictional policy?', 5)
            assert result['status'] == ('no_eligible_documents' if status == 'no_eligible_documents' else 'no_matches')
            assert result['fallback_reason'] == reason and result['matches'] == []
            assert 'candidate_order' not in result
            assert 'WITHHELD FICTIONAL PASSAGE' not in json.dumps(result)
        finally:
            await service.close()
    run_async(scenario())


def test_benchmark_rejects_small_or_leaking_datasets():
    from scripts.benchmark_online_retrieval import validate_cases
    cases = [(dict(id=str(i), question=str(i), intent_group=str(i), split='calibration', relevant=True), 'rev', 'run') for i in range(2)]
    with pytest.raises(ValueError, match='need_100'):
        validate_cases(cases)
    validate_cases(cases, collect_only=True)
    cases[1][0].update(intent_group='0', split='holdout')
    with pytest.raises(ValueError, match='leaks'):
        validate_cases(cases, collect_only=True)


@pytest.mark.parametrize('collect_only', [False, True])
def test_benchmark_runs_filtered_latency_and_only_certifies_labeled_cases(tmp_path, monkeypatch, collect_only):
    import json
    from scripts import benchmark_online_retrieval as bench
    manifest = tmp_path / 'manifest.json'
    case_path = tmp_path / 'cases.json'
    case_path.write_text('{}')
    manifest.write_text(json.dumps({'datasets': [{'package': str(tmp_path), 'cases': str(case_path)}]}))
    cases = []
    for i in range(100):
        split = 'calibration' if i < 40 else 'holdout' if i < 80 else 'regression'
        supported = i % 2 == 0
        cases.append(dict(id=str(i), question=str(i), intent_group=str(i), split=split,
            relevant=supported, category='detail' if supported else 'unsupported',
            required_groups=[[0]] if supported else [], candidate_labels={'chunk': supported}))
    files = {'metadata.json': b'{}'}
    monkeypatch.setattr(bench, 'model_fingerprint', lambda *_: 'embedding')
    monkeypatch.setattr(bench, 'load_cases', lambda *_: cases)
    monkeypatch.setattr(bench, 'configuration', lambda *_: {})
    monkeypatch.setattr(bench.packages, 'load_package', lambda *_a, **_k: ({'revision_id': 'rev'}, {}, files))
    monkeypatch.setattr(bench.packages, 'snapshot_files', lambda *_: files)
    monkeypatch.setattr(bench, 'database_state', AsyncMock(return_value={'run_id': 'run'}))
    instances = []
    class Service:
        def __init__(self, **kwargs):
            self._reranker = SimpleNamespace(fingerprint='reranker', profile=None)
            self.public_calls = 0
            self.closed = False
            instances.append(self)
        async def start(self):
            pass
        async def close(self):
            self.closed = True
        async def _retrieve(self, question, top_k, *, assessment):
            assert assessment is False
            match = dict(chunk_id='chunk', chunk_index=0, revision_id='rev', run_id='run',
                         rerank_score=3. if int(question) % 2 == 0 else -2., cosine_distance=.1)
            return dict(status='matches_found', matches=[match], candidate_order=[match],
                        corpus_fingerprint='corpus', rerank_seconds=.01)
        async def retrieve(self, question, top_k):
            self.public_calls += 1
            assert (self._reranker.profile is None) == collect_only
            return dict(corpus_fingerprint='corpus')
    monkeypatch.setattr(bench, 'RetrievalService', Service)
    args = SimpleNamespace(manifest=manifest, model_path=tmp_path, model_revision='a'*40,
                           rerank=True, collect_only=collect_only, requests=100)
    report = run_async(bench.benchmark(args))
    assert report['rollout_ready'] is (not collect_only)
    assert report['corpus_fingerprint'] == 'corpus'
    assert instances[0].public_calls == 100 and instances[0].closed
    assert all('_case' not in row for row in report['cases'])
