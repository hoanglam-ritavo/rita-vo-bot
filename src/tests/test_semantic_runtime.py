"""Synthetic expansion safety and public orchestration; no real models/publications."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.database.runtime import run_async
from src.ingestion import embeddings
from src.intent import IntentSettings
from src.retrieval import RetrievalService, RetrievalError, storage
from src.retrieval.reranking import OnnxReranker, RerankSettings
from src.retrieval.semantic_config import from_arguments, from_environment
from src.retrieval.semantics import (SemanticSettings, SemanticExpander,
                                     expansion_candidates, validate_artifact)


def concept(identity='arrival', canonical='đi trễ', aliases=None, **changes):
    return dict(id=identity, canonical=canonical, aliases=aliases or ['đi muộn'],
                requires_any=[], excludes_any=[], styles=['conditions']) | changes


def artifact(*concepts):
    return dict(schema_version=1, reviewed=True, review_notes='Synthetic independent review.',
                package_fingerprint='a' * 64, concepts=list(concepts) or [concept()])


@pytest.mark.parametrize('question,expected', [
    ('Tôi đi muộn 15 phút nhưng không xin phép?', 'Tôi đi trễ 15 phút nhưng không xin phép?'),
    ('Nhân viên thử việc đi muộn lần thứ 4 thì sao?', 'Nhân viên thử việc đi trễ lần thứ 4 thì sao?'),
    ('  Tôi\n ĐI MUỘN?', 'Tôi đi trễ?'),
])
def test_rewrites_phrase_and_preserves_conditions(question, expected):
    assert expansion_candidates(artifact(), question) == [expected]


def test_boundaries_ambiguity_and_context_guards():
    assert expansion_candidates(artifact(), 'xđi muộny') == []
    ambiguous = artifact(concept(), concept('other', 'làm thêm giờ'))
    assert expansion_candidates(ambiguous, 'đi muộn') == []
    guarded = artifact(concept(requires_any=['nhân viên'], excludes_any=['khách']))
    assert expansion_candidates(guarded, 'đi muộn') == []
    assert expansion_candidates(guarded, 'khách và nhân viên đi muộn') == []
    assert expansion_candidates(guarded, 'nhân viên đi muộn') == ['nhân viên đi trễ']


def test_two_alternate_budget_style_priority_and_determinism():
    data = artifact(concept('a', 'xin phép', ['đề nghị nghỉ'], styles=['procedure']),
                    concept('b', 'đi trễ', ['đi muộn'], styles=['conditions']),
                    concept('c', 'thẻ', ['card'], styles=['detail']))
    question = 'đề nghị nghỉ khi đi muộn và mất card'
    variants = expansion_candidates(data, question)
    assert len(variants) == len(set(variants)) == 2
    assert variants[0] == 'xin phép khi đi muộn và mất card'
    assert expansion_candidates(data, question, styles=['detail'])[0] == 'đề nghị nghỉ khi đi muộn và mất thẻ'
    data['concepts'].reverse()
    assert expansion_candidates(data, question) == variants


@pytest.mark.parametrize('change,code', [
    ({'reviewed': False}, 'semantic_review_required'),
    ({'review_notes': ''}, 'semantic_review_required'),
    ({'package_fingerprint': 'old'}, 'invalid_semantic_artifact'),
    ({'extra': 'not allowed'}, 'invalid_semantic_artifact'),
    ({'schema_version': True}, 'invalid_semantic_artifact'),
    ({'concepts': [concept(styles=['unknown'])]}, 'invalid_semantic_artifact'),
    ({'concepts': [concept(), concept()]}, 'invalid_semantic_artifact'),
])
def test_review_and_shape_gates(change, code):
    with pytest.raises(ValueError, match=code):
        validate_artifact(artifact() | change)
    assert validate_artifact(artifact() | {'reviewed': False}, require_reviewed=False)['reviewed'] is False


def test_settings_and_explicit_config(tmp_path):
    assert from_environment({}) == SemanticSettings()
    assert from_arguments(SimpleNamespace()) == SemanticSettings()
    path = tmp_path / 'lexicon.json'
    assert from_environment({'SEMANTIC_MODE': 'expand', 'SEMANTIC_ARTIFACT_PATH': str(path)}) == SemanticSettings('expand', path)
    for values in ({'SEMANTIC_MODE': 'unknown'}, {'SEMANTIC_MODE': 'expand'}):
        with pytest.raises(ValueError):
            from_environment(values)
    with pytest.raises(ValueError, match='semantic_requires_reranker'):
        RetrievalService(model_path='unused', model_revision='a' * 40, semantic=SemanticSettings('expand', path))


def test_load_binds_package_and_pipeline_identity(tmp_path, monkeypatch):
    from src.semantics import corpus
    monkeypatch.setattr(corpus, 'snapshot_fingerprint', lambda: 'a' * 64)
    path = tmp_path / 'lexicon.json'
    data = artifact()
    path.write_text(json.dumps(data), encoding='utf-8')
    expander = SemanticExpander(SemanticSettings('expand', path))
    expander.start()
    assert expander.expansion('đi muộn 15 phút') == ['đi trễ 15 phút']
    semantic_identity = expander.effective_fingerprint('base')
    assert semantic_identity != 'base'
    assert semantic_identity != expander.effective_fingerprint('base', intent_digest='assist:none')
    assert SemanticExpander().effective_fingerprint('base') == 'base'
    expander.close()
    assert expander.expansion('đi muộn') == []
    monkeypatch.setattr(corpus, 'snapshot_fingerprint', lambda: 'b' * 64)
    with pytest.raises(ValueError, match='semantic_package_mismatch'):
        expander.start()
    assert expander.artifact is None


@pytest.mark.parametrize('raw', [
    '{"reviewed": false, "reviewed": true}',
    '{"value": NaN}', '{"value": Infinity}',
])
def test_loader_rejects_ambiguous_json_without_disclosing_content(tmp_path, raw):
    path = tmp_path / 'lexicon.json'
    path.write_text(raw, encoding='utf-8')
    expander = SemanticExpander(SemanticSettings('expand', path))
    with pytest.raises(ValueError, match='^invalid_semantic_artifact$'):
        expander.start()
    assert expander.artifact is None and expander.fingerprint is None


def install_runtime(monkeypatch, tmp_path, *, intent=None):
    from src.semantics import corpus
    monkeypatch.setattr(corpus, 'snapshot_fingerprint', lambda: 'a' * 64)
    path = tmp_path / 'lexicon.json'
    path.write_text(json.dumps(artifact()), encoding='utf-8')
    tokenizer = Mock(return_value={'input_ids': [1]})
    tokenizer.model_max_length = 512
    model = SimpleNamespace(max_seq_length=512, tokenizer=tokenizer,
        encode=Mock(return_value=[SimpleNamespace(tolist=lambda: [1.] + [0.] * 383)]))
    monkeypatch.setattr(embeddings, 'load_model', lambda *a, **k: model)
    monkeypatch.setattr(embeddings, 'model_fingerprint', lambda _: 'embedding')
    monkeypatch.setattr(OnnxReranker, 'start', lambda self: setattr(self, 'fingerprint', 'base'))
    score = Mock(return_value=([1.], 1))
    monkeypatch.setattr(OnnxReranker, 'score', score)
    found = dict(status='matches_found', corpus_fingerprint='published', matches=[
        dict(chunk_id='chunk', chunk_index=0, run_id='run', revision_id='revision',
             cosine_distance=.2, content='Fictional evidence.', source_spans=['preserved'])])
    search = AsyncMock(side_effect=lambda *args, **kwargs: deepcopy(found))
    monkeypatch.setattr(storage, 'search', search)
    service = RetrievalService(model_path='unused', model_revision='a' * 40,
        rerank=RerankSettings(enabled=True), semantic=SemanticSettings('expand', path), intent=intent)
    return service, model, search, score


def test_service_keeps_original_rerank_query_and_requires_new_profile(tmp_path, monkeypatch):
    service, model, search, score = install_runtime(monkeypatch, tmp_path)
    async def scenario():
        await service.start()
        try:
            service._reranker.profile = dict(fingerprint='base', corpus_fingerprint='published', threshold=0., holdout_passed=True)
            result = await service.retrieve('đi muộn 15 phút không xin phép')
            assert result['fallback_reason'] == 'relevance_unavailable' and result['matches'] == []
            assert 'candidate_order' not in result
            assert score.call_args.args[0] == 'đi muộn 15 phút không xin phép'
            assert search.call_args.args[3] == 30 and len(search.call_args.kwargs['extra_vectors']) == 1
            encoded = [call.args[0][0] for call in model.encode.call_args_list]
            assert encoded == ['query: đi muộn 15 phút không xin phép', 'query: đi trễ 15 phút không xin phép']
            service._reranker.profile['fingerprint'] = service._reranker.fingerprint
            result = await service.retrieve('đi muộn 15 phút không xin phép')
            assert result['matches'][0]['source_spans'] == ['preserved']
            assert not {'semantic', 'variants', 'intent'} & result.keys()
        finally:
            await service.close()
    run_async(scenario())


def test_overlength_alternate_is_skipped_without_truncating_original(tmp_path, monkeypatch):
    service, model, search, score = install_runtime(monkeypatch, tmp_path)
    model.tokenizer.side_effect = lambda text, **kwargs: {'input_ids': [1] * (513 if 'đi trễ' in text else 1)}
    async def scenario():
        await service.start()
        try:
            await service.retrieve('đi muộn')
            assert 'extra_vectors' not in search.call_args.kwargs
            assert model.encode.call_count == 1
            assert score.call_args.args[0] == 'đi muộn'
        finally:
            await service.close()
    run_async(scenario())


def test_start_review_failure_is_safe_and_clears_readiness(tmp_path, monkeypatch):
    service, model, search, score = install_runtime(monkeypatch, tmp_path)
    path = service._semantic.settings.artifact_path
    path.write_text(json.dumps(artifact() | {'reviewed': False}), encoding='utf-8')
    async def scenario():
        with pytest.raises(RetrievalError, match='semantic_review_required'):
            await service.start()
        assert not service.started and service._semantic.artifact is None
        with pytest.raises(RetrievalError, match='service_not_started'):
            await service.retrieve('question')
        await service.close()
    run_async(scenario())
    model.encode.assert_not_called()
    search.assert_not_called()


@pytest.mark.parametrize('status,styles,expected', [
    ('classified', ['procedure'], ['procedure']),
    ('uncertain', ['conditions'], ()), ('unavailable', [], ()),
])
def test_assistance_reuses_original_vector_and_never_blocks_search(tmp_path, monkeypatch, status, styles, expected):
    service, model, search, score = install_runtime(monkeypatch, tmp_path, intent=IntentSettings('assist'))
    # The classifier's own load/acceptance tests are separate; this exercises the consumer.
    monkeypatch.setattr(service._intent, 'start', lambda _: None)
    predict = Mock(return_value=dict(status=status, source='type_b', styles=styles))
    monkeypatch.setattr(service._intent, 'predict', predict)
    expand = Mock(return_value=[])
    monkeypatch.setattr(service._semantic, 'expansion', expand)
    async def scenario():
        await service.start()
        try:
            assert service._reranker.fingerprint == service._semantic.effective_fingerprint('base', intent_digest='assist:none')
            await service.retrieve('đi muộn')
            expand.assert_called_once_with('đi muộn', styles=expected)
            predict.assert_called_once_with([1.] + [0.] * 383)
            search.assert_awaited_once()
            assert model.encode.call_count == 1
        finally:
            await service.close()
    run_async(scenario())


def test_http_keeps_wire_contract_and_hides_expansion(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.app import create_app
    from src.api.config import Settings
    from src.api.service import ApiService
    service, model, search, score = install_runtime(monkeypatch, tmp_path)
    settings = Settings(model_path=tmp_path, model_revision='a' * 40,
        rerank=service.rerank_settings, semantic=service._semantic.settings)
    api = ApiService(settings, retrieval=service, database_check=AsyncMock(), database_close=AsyncMock())
    with TestClient(create_app(settings, service_factory=lambda _: api)) as client:
        response = client.post('/api/v1/retrieve', json={'text': 'đi muộn', 'top_k': 5})
        assert response.status_code == 200
        data = response.json()
        assert set(data) == {'status', 'matches', 'fallback_reason', 'relevance', 'timings'}
        assert data['fallback_reason'] == 'relevance_unavailable'
        assert len(search.call_args.kwargs['extra_vectors']) == 1
        assert score.call_args.args[0] == 'đi muộn'
