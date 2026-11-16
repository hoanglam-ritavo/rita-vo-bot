"""Fictional cross-document and paired-promotion regressions, no real models/DB."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.database.runtime import run_async
from src.retrieval import semantic_evaluation as evaluation
from scripts import benchmark_semantic_retrieval as benchmark


DOC = '11111111-1111-4111-8111-111111111111'
REV = '22222222-2222-4222-8222-222222222222'
OTHER_DOC = '33333333-3333-4333-8333-333333333333'
OTHER_REV = '44444444-4444-4444-8444-444444444444'
GOOD = '55555555-5555-4555-8555-555555555555'
BAD = '66666666-6666-4666-8666-666666666666'


def evidence_ref(document=DOC, revision=REV, index=0):
    return {'document_id': document, 'revision_id': revision, 'chunk_index': index}


def case(index=0, split='calibration', relevant=True):
    return {'id': f'case-{index}', 'question': f'Fictional source question {index}',
            'domain': 'fictional-policy', 'intent_group': f'family-{index}', 'split': split,
            'relevant': relevant, 'critical': False, 'top1_required': False,
            'wording': 'direct' if index % 4 == 0 else 'paraphrase',
            'unit_ids': ['unit-one'] if relevant else [],
            'required_groups': [[evidence_ref()]] if relevant else [],
            'candidate_labels': {GOOD: relevant, BAD: False}}


def dataset():
    cases = [case(i, 'calibration' if i < 100 else 'holdout' if i < 200 else 'regression',
                  relevant=i % 2 == 0) for i in range(300)]
    cases[0]['critical'] = cases[100]['critical'] = True
    return {'schema_version': 1, 'reviewed': True, 'review_notes': 'Fictional labels only.',
            'required_units': ['unit-one'], 'cases': cases, 'packages': []}


def result(good_score=2., bad_score=-2., *, include_good=True):
    good = dict(evidence_ref(), chunk_id=GOOD, run_id='run', rerank_score=good_score,
                cosine_distance=.2, content='Fictional policy condition')
    bad = dict(evidence_ref(index=1), chunk_id=BAD, run_id='run', rerank_score=bad_score,
               cosine_distance=.1, content='Fictional unrelated material')
    candidates = [good, bad] if include_good else [bad]
    return {'status': 'matches_found', 'matches': sorted(candidates, key=lambda m: -m['rerank_score']),
            'candidate_order': [{key: m[key] for key in ('chunk_id', 'revision_id', 'chunk_index', 'run_id')}
                                for m in reversed(candidates)],
            'corpus_fingerprint': 'corpus', 'reranker_fingerprint': 'pipeline', 'rerank_seconds': .01}


def test_review_gate_and_family_split_isolation():
    data = dataset()
    assert len(evaluation.validate_dataset(data)) == 300
    data['reviewed'] = False
    with pytest.raises(ValueError, match='human_review_required'):
        evaluation.validate_dataset(data)
    evaluation.validate_dataset(data, collect_only=True)
    data['cases'][100]['intent_group'] = data['cases'][0]['intent_group']
    with pytest.raises(ValueError, match='family_split_leak'):
        evaluation.validate_dataset(data, collect_only=True)


@pytest.mark.parametrize('mutation,code', [
    (lambda d: d['cases'].pop(), 'need_300'),
    (lambda d: d['required_units'].append('uncovered-unit'), 'unit_coverage'),
    (lambda d: d['cases'][1].update(question=d['cases'][0]['question'].upper()), 'duplicate'),
    (lambda d: d['cases'][0].update(critical=1), 'flags_required'),
    (lambda d: d['cases'][0]['required_groups'][0][0].update(chunk_index=True), 'evidence_reference'),
    (lambda d: d['cases'][0].update(candidate_labels={GOOD: 1}), 'candidate_labels'),
])
def test_dataset_rejects_invalid_declarations(mutation, code):
    data = dataset()
    mutation(data)
    with pytest.raises(ValueError, match=code):
        evaluation.validate_dataset(data)


def test_cross_document_evidence_uses_and_or_groups():
    sample = case()
    sample['required_groups'].append([evidence_ref(OTHER_DOC, OTHER_REV, 7), evidence_ref(OTHER_DOC, OTHER_REV, 8)])
    first = evaluation.evidence(sample, result()['matches'])
    assert first['complete'] is False and first['group_recall'] == .5
    matches = result()['matches'] + [evidence_ref(OTHER_DOC, OTHER_REV, 8)]
    assert evaluation.evidence(sample, matches) == {'group_ranks': [1, 3], 'complete': True, 'group_recall': 1.}
    # The same chunk index in an unrelated revision must not satisfy the second source.
    matches[-1]['revision_id'] = REV
    assert evaluation.evidence(sample, matches)['complete'] is False


def test_candidate_union_is_labelled_once_across_arms():
    sample = case()
    evaluation.check_labels(sample, {'baseline': result(include_good=False), 'semantic': result()})
    sample['candidate_labels'].pop(GOOD)
    with pytest.raises(ValueError, match='complete_union'):
        evaluation.check_labels(sample, {'baseline': result(include_good=False), 'semantic': result()})


def test_candidate_recall_uses_real_offline_order_and_filtered_metrics():
    sample = case()
    sample['top1_required'] = True
    report = evaluation.metrics(sample, result(good_score=1., bad_score=2.), threshold=3.)
    assert report['candidate_complete30'] is True
    assert report['candidate_group_recall30'] == 1
    assert report['complete5'] is False and report['false_empty'] is True
    assert report['ndcg5'] == 0 and report['fallback_reason'] == 'below_relevance_threshold'
    broken = result()
    broken.pop('candidate_order')
    with pytest.raises(ValueError, match='candidate_diagnostics_required'):
        evaluation.metrics(sample, broken, 0)


@pytest.mark.parametrize('status,reason', [
    ('no_matches', 'no_candidates'), ('no_eligible_documents', 'no_eligible_documents'),
])
def test_empty_candidate_diagnostics_preserve_distinct_reason(status, reason):
    empty = result()
    empty.update(status=status, matches=[], candidate_order=[])
    for threshold in (None, 1.):
        assert evaluation.metrics(case(), empty, threshold)['fallback_reason'] == reason


def test_threshold_uses_only_calibration_negatives():
    calibration = case()
    holdout = case(1, 'holdout')
    threshold = evaluation.threshold_for([calibration, holdout], [result(bad_score=-1), result(bad_score=100)])
    assert threshold > -1 and threshold < 0
    assert evaluation.threshold_for([holdout], [result()]) is None


def test_absolute_gates_check_full_public_range_and_critical_unsupported():
    data = dataset()
    rows = [result(good_score=2. if c['relevant'] else -3) for c in data['cases']]
    threshold = evaluation.threshold_for(data['cases'], rows)
    report = evaluation.summarize(data['cases'], rows, [.1] * 100, threshold)
    assert report['absolute_gates_passed'] is True
    assert report['summary']['complete5'] == 1
    assert report['summary']['p95_seconds'] == .1
    # A holdout irrelevant passage below rank five but within public top20 must fail.
    rows[100]['matches'].extend([dict(rows[100]['matches'][1], rerank_score=3.)])
    failed = evaluation.summarize(data['cases'], rows, [.1] * 100, threshold)
    assert failed['gates']['no_irrelevant_returned'] is False
    # No p95 claim with fewer than the mandatory warmed request count.
    assert evaluation.summarize(data['cases'], rows, [.1] * 99, threshold)['gates']['latency'] is False


def test_comparison_cannot_hide_domain_regression():
    reference = {'summary': {'holdout_macro_ndcg5': .4, 'complete5': .9,
                  'domains': {'large': {'complete5': .9}, 'small': {'complete5': 1.}}}}
    candidate = {'absolute_gates_passed': True,
                 'summary': {'holdout_macro_ndcg5': .8, 'complete5': .95,
                 'domains': {'large': {'complete5': 1.}, 'small': {'complete5': .9}}}}
    for report in (reference, candidate):
        report['summary']['holdout_domains'] = copy.deepcopy(report['summary']['domains'])
    report = evaluation.compare(reference, candidate)
    assert report['holdout_macro_ndcg5_improved'] is True
    assert report['promotion_ready'] is False


def test_holdout_domain_regression_cannot_be_hidden_by_regression_split():
    before = {'absolute_gates_passed': True, 'summary': {'holdout_macro_ndcg5': .4, 'complete5': .9,
        'domains': {'topic': {'complete5': .9}}, 'holdout_domains': {'topic': {'complete5': 1.}}}}
    after = copy.deepcopy(before)
    after['summary'].update(holdout_macro_ndcg5=.6)
    after['summary']['holdout_domains']['topic']['complete5'] = .9
    assert evaluation.compare(before, after)['promotion_ready'] is False


@pytest.mark.parametrize('score', [float('nan'), float('inf'), None, True])
def test_nonfinite_or_invalid_positive_scores_cannot_silently_pass(score):
    rows = result()
    rows['matches'][0]['rerank_score'] = score
    with pytest.raises(ValueError, match='invalid_semantic_rerank_scores'):
        evaluation.threshold_for([case()], [rows])


def test_direct_paraphrase_and_critical_coverage_are_required():
    data = dataset()
    for c in data['cases']:
        c['wording'] = 'direct'
    with pytest.raises(ValueError, match='direct_and_paraphrase'):
        evaluation.validate_dataset(data)
    data = dataset()
    for c in data['cases']:
        c['critical'] = False
    with pytest.raises(ValueError, match='critical_cases_required'):
        evaluation.validate_dataset(data)


def test_collection_never_emits_profiles_and_output_cannot_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, 'REPOSITORY_ROOT', tmp_path)
    output = tmp_path / '.tmp' / 'collection.json'
    args = SimpleNamespace(output=output)
    benchmark.write_report(args, {'collect_only': True, 'pipelines': {}})
    assert output.exists()
    assert list(output.parent.glob('*.profile.json')) == []
    with pytest.raises(ValueError, match='already_exists'):
        benchmark.write_report(args, {'collect_only': True})


def test_intent_profile_cannot_skip_failed_semantic_stage(tmp_path):
    args = SimpleNamespace(output=tmp_path / 'comparison.json')
    passed = {'absolute_gates_passed': True, 'fingerprint': 'fictional', 'threshold': 1.}
    report = {'collect_only': False, 'corpus_fingerprint': 'fictional-corpus',
        'pipelines': {'baseline': dict(passed), 'semantic': dict(passed), 'intent': dict(passed)},
        'comparisons': {'semantic_vs_baseline': {'promotion_ready': False},
                        'intent_vs_semantic': {'promotion_ready': True}}}
    benchmark.write_report(args, report)
    assert args.output.exists()
    assert args.output.with_name('comparison.baseline.profile.json').exists()
    assert not args.output.with_name('comparison.semantic.profile.json').exists()
    assert not args.output.with_name('comparison.intent.profile.json').exists()


def test_unreviewed_dataset_rejected_before_model_or_database(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(evaluation, 'REPOSITORY_ROOT', tmp_path)
    path = tmp_path / '.tmp' / 'draft.json'
    path.parent.mkdir()
    draft = dataset()
    draft['reviewed'] = False
    path.write_text(json.dumps(draft), encoding='utf-8')
    forbidden = lambda *a, **k: pytest.fail('Review must precede model/database access')
    monkeypatch.setattr(benchmark, 'model_fingerprint', forbidden)
    monkeypatch.setattr(benchmark, 'publication_snapshot', forbidden)
    assert benchmark.main(['--dataset', str(path), '--model-path', str(tmp_path / 'model'),
        '--model-revision', 'a' * 40, '--semantic-artifact', 'unused.json', '--rerank',
        '--output', str(path.parent / 'result.json')]) == 2
    assert json.loads(capsys.readouterr().out)['rollout_ready'] is False
    assert not (path.parent / 'result.json').exists()


def fake_inputs(tmp_path):
    sample = case()
    source = tmp_path / 'dataset.json'
    source.write_bytes(b'dataset')
    args = SimpleNamespace(dataset=source, model_path=tmp_path / 'model', model_revision='a' * 40,
        collect_only=True, requests=0, rerank=True, rerank_model_path=tmp_path / 'rerank',
        rerank_candidates=30, rerank_batch_size=4, rerank_profile=None,
        semantic_artifact=tmp_path / 'lexicon.json', intent_artifact=None)
    inputs = ({}, [sample], [], b'dataset', {})
    return args, inputs


def install_fake_services(monkeypatch, *, corpus_drift=False, cleanup_failure=False):
    instances = []
    class FakeService:
        def __init__(self, **kwargs):
            self._reranker = SimpleNamespace(fingerprint='pipeline', profile=None)
            self._intent = SimpleNamespace(artifact={})
            self.closed = False
            instances.append(self)
        async def start(self):
            pass
        async def _retrieve(self, *args, **kwargs):
            answer = result()
            if corpus_drift and len(instances) > 1 and self is instances[-1]:
                answer['corpus_fingerprint'] = 'changed'
            return answer
        async def close(self):
            self.closed = True
            if cleanup_failure:
                raise RuntimeError('fictional cleanup error')
    monkeypatch.setattr(benchmark, 'RetrievalService', FakeService)
    monkeypatch.setattr(benchmark, 'model_fingerprint', lambda p: 'model')
    monkeypatch.setattr(benchmark, 'publication_snapshot', AsyncMock(return_value='pins'))
    return instances


def test_collection_report_contains_no_question_or_excerpt(tmp_path, monkeypatch):
    args, inputs = fake_inputs(tmp_path)
    services = install_fake_services(monkeypatch)
    report = run_async(benchmark.benchmark(args, inputs))
    encoded = json.dumps(report)
    assert report['collect_only'] is True and report['rollout_ready'] is False
    assert 'Fictional source question' not in encoded and 'Fictional policy condition' not in encoded
    assert all(service.closed for service in services)
    assert 'candidate_collection' in report['pipelines']['semantic']


@pytest.mark.parametrize('failure', ['corpus', 'dataset', 'publication', 'cleanup', 'artifact'])
def test_drift_and_cleanup_fail_without_acceptance(tmp_path, monkeypatch, failure):
    args, inputs = fake_inputs(tmp_path)
    instances = install_fake_services(monkeypatch, corpus_drift=failure == 'corpus',
                                      cleanup_failure=failure == 'cleanup')
    if failure == 'dataset':
        args.dataset.write_bytes(b'changed')
    if failure == 'publication':
        monkeypatch.setattr(benchmark, 'publication_snapshot', AsyncMock(side_effect=['pins', 'changed']))
    if failure == 'artifact':
        path = tmp_path / 'artifact.json'
        path.write_bytes(b'changed')
        inputs = (*inputs[:4], {path: b'original'})
    with pytest.raises(ValueError, match='changed|cleanup_failed'):
        run_async(benchmark.benchmark(args, inputs))
    assert all(service.closed for service in instances)
