"""Learned passage scoring boundaries with fictional inputs; no database/models."""
import copy
import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from src.retrieval.scoring import (FEATURES, VERSION, apply_scoring, passage_features,
                                   scoring_digest, validate_scoring)
from src.retrieval.reranking import OnnxReranker, RerankSettings, load_profile, profile_threshold
from scripts import benchmark_semantic_retrieval as benchmark
from src.database.runtime import run_async
from src.retrieval import RetrievalService, storage
from unittest.mock import AsyncMock


def scoring():
    value = dict(version=VERSION, feature_names=list(FEATURES), means=[0.] * 8,
                 scales=[1.] * 8, weights=[1., 0., 0., 0., 0., 0., 0., 2.], bias=-1.)
    value['digest'] = scoring_digest(value)
    return value


def test_features_preserve_numeric_and_guard_distinctions():
    concepts = [dict(canonical='travel allowance', aliases=['travel payment'],
                     requires_any=['employee'], excludes_any=['visitor'])]
    question = 'employee travel payment 15 days'
    good = passage_features(question, 'employee travel allowance 15 days', [2., 4.], .2, concepts)
    adjacent = passage_features(question, 'visitor travel allowance 10 days', [2., 4.], .2, concepts)
    assert good[:5] == pytest.approx([4., 3., 1., 2., .8])
    assert good[6:] == [1., 1.]
    assert adjacent[6:] == [0., 0.]
    assert good[5] > adjacent[5]
    assert passage_features('unrelated', '', [0.], 1.)[6:] == [1., 1.]


@pytest.mark.parametrize('mutate', [
    lambda s: s.update(weights=[math.nan] * 8),
    lambda s: s.update(scales=[0.] * 8),
    lambda s: s.update(bias=True),
    lambda s: s.update(feature_names=list(reversed(FEATURES))),
    lambda s: s.update(weights=[1.] * 8),
    lambda s: s.update(extra='unsupported'),
])
def test_scoring_rejects_tampering_and_invalid_numbers(mutate):
    value = scoring()
    mutate(value)
    with pytest.raises(ValueError, match='invalid_relevance_scoring'):
        validate_scoring(value)


def test_numeric_scoring_and_schema_three_profile_binding(tmp_path):
    value = scoring()
    assert apply_scoring([[3., 0., 0., 1., .8, .5, 1., 1.]], value) == [4.]
    path = tmp_path / 'profile.json'
    profile = dict(schema_version=3, fingerprint='new-pipeline', corpus_fingerprint='new-corpus',
                   threshold=3., holdout_passed=True, scoring=value)
    path.write_text(json.dumps(profile))
    loaded = load_profile(path)
    assert profile_threshold(loaded, 'new-pipeline', 'new-corpus') == 3.
    assert profile_threshold(loaded, 'old-pipeline', 'new-corpus') is None
    assert profile_threshold(loaded, 'new-pipeline', 'old-corpus') is None
    for changes in ({'scoring': None}, {'schema_version': 2}, {'holdout_passed': 'true'}):
        path.write_text(json.dumps(profile | changes))
        with pytest.raises(ValueError, match='invalid_relevance_profile'):
            load_profile(path)


def test_window_features_keep_owners_and_existing_max_in_same_batches():
    class Tokenizer:
        def encode(self, text, **kwargs):
            return list(range(10, 10 + int(text)))
        def num_special_tokens_to_add(self, **kwargs):
            return 4
        def pad(self, batch, **kwargs):
            length = max(len(row['input_ids']) for row in batch)
            return {key: np.array([row[key] + [0] * (length - len(row[key])) for row in batch])
                    for key in ('input_ids', 'attention_mask')}
    reranker = OnnxReranker(RerankSettings(enabled=True, batch_size=2))
    reranker.tokenizer = Tokenizer()
    reranker.session = SimpleNamespace(get_inputs=lambda: [SimpleNamespace(name=n)
        for n in ('input_ids', 'attention_mask')],
        run=lambda _, inputs: [inputs['input_ids'].max(axis=1).reshape(-1, 1)])
    matches = [dict(content='1300', cosine_distance=.2), dict(content='2', cosine_distance=.9)]
    before, count = reranker.score('10', matches)
    scores, windows, features = reranker.score_passages('10', matches)
    assert scores == before == [1309., 19.]
    assert count == windows == 4
    assert [row[3] for row in features] == [3., 1.]
    assert features[1][1:3] == [19., 0.]
    reranker.scoring = scoring()
    assert reranker.score_passages('10', matches)[0] == apply_scoring(features, scoring())


def test_schema_three_writer_requires_existing_promotion_gates(tmp_path):
    passed = dict(absolute_gates_passed=True, fingerprint='synthetic', threshold=1.)
    report = dict(collect_only=False, corpus_fingerprint='synthetic-corpus', scoring=scoring(),
        pipelines={'baseline': copy.deepcopy(passed), 'semantic': copy.deepcopy(passed)},
        comparisons={'semantic_vs_baseline': {'promotion_ready': False}})
    output = tmp_path / 'report.json'
    benchmark.write_report(SimpleNamespace(output=output), report)
    assert json.loads(output.with_name('report.baseline.profile.json').read_text())['schema_version'] == 3
    assert not output.with_name('report.semantic.profile.json').exists()


def test_private_feature_collection_and_empty_candidates_never_leak_publicly(monkeypatch):
    search = AsyncMock(return_value=dict(status='no_eligible_documents', matches=[], corpus_fingerprint='c'))
    monkeypatch.setattr(storage, 'search', search)
    async def scenario():
        service = RetrievalService(model_path='unused', model_revision='a' * 40)
        service._model = object()
        service._encode = lambda _: [0.] * 384
        offline = await service._retrieve('fictional question', assessment=False, collect_scoring_features=True)
        assert offline['scoring_features'] == {}
        public = await service.retrieve('fictional question')
        assert 'scoring_features' not in public
        assert 'candidate_order' not in public
        with pytest.raises(ValueError, match='private_scoring_diagnostics_only'):
            await service._retrieve('fictional question', assessment=True, collect_scoring_features=True)
    run_async(scenario())


def test_scoring_development_family_cannot_enter_acceptance(tmp_path, monkeypatch):
    from src.retrieval import semantic_evaluation
    monkeypatch.setattr(semantic_evaluation, 'REPOSITORY_ROOT', tmp_path)
    monkeypatch.setattr(benchmark, 'REPOSITORY_ROOT', tmp_path)
    work = tmp_path / '.tmp'
    work.mkdir()
    source, candidate, training = [work / name for name in ('data.json', 'score.json', 'training.json')]
    sample = dict(id='new-case', question='fictional unsupported question', domain='fictional',
        intent_group='exposed-family', split='holdout', relevant=False, critical=False,
        top1_required=False, unit_ids=[], wording='control', required_groups=[], candidate_labels={})
    data = dict(schema_version=1, reviewed=False, required_units=['unit'], cases=[sample],
                packages=[dict(path='chunks/example', sha256='hash')])
    source.write_text(json.dumps(data))
    candidate.write_text(json.dumps(scoring()))
    training.write_text(json.dumps(dict(scoring_digest=scoring()['digest'], training_only=True,
        development_intent_groups=['exposed-family'], development_case_ids=['old-case'])))
    package = tmp_path / 'chunks/example'
    monkeypatch.setattr(benchmark.packages, 'load_package', lambda *a, **k:
        (dict(document_id='document', revision_id='revision'), dict(chunks=[]), {}))
    monkeypatch.setattr(benchmark.packages, 'files_hash', lambda _: 'hash')
    monkeypatch.setattr(benchmark.packages, 'package_paths', lambda _: [package])
    args = SimpleNamespace(dataset=source, output=work / 'result.json', collect_only=True,
        requests=0, rerank=True, rerank_profile=None, semantic_artifact=work / 'lexicon.json',
        intent_artifact=None, model_path=work / 'model', scoring_candidate=candidate,
        scoring_training_report=training)
    with pytest.raises(ValueError, match='scoring_development_acceptance_leak'):
        benchmark.load_inputs(args)
