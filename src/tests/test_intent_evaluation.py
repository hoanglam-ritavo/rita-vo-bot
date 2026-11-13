"""Synthetic training/promotion boundary tests, not real-model acceptance evidence."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from scripts import intent_contract as contract
from scripts import train_intent as trainer
from scripts import benchmark_intent as bench
from src.intent.runtime import SOURCES, STYLES, GATES, identity, digest, validate_artifact
from src.database.runtime import run_async


def dataset():
    cases = []
    # Fictional multi-label examples; group separation is what this fixture exercises.
    for source in SOURCES:
        for i in range(100):
            cases.append({'id': f'{source}-{i}', 'question': f'Fictional question {source} {i}',
                          'intent_group': f'family-{i // 5}',
                          'split': 'train' if i < 60 else 'calibration' if i < 80 else 'holdout',
                          'source': source, 'styles': [s for j, s in enumerate(STYLES) if (i + j) % 5 != 0]
                          if source in ('type_a', 'mixed') else [], 'critical': i % 5 == 0})
    return {'schema_version': 1, 'reviewed': True, 'review_notes': 'Fictional test labels only.', 'cases': cases}


def test_review_required_and_group_leaks():
    data = dataset()
    assert len(contract.validate_dataset(data)) == 600
    data['reviewed'] = False
    with pytest.raises(ValueError, match='human_review_required'):
        contract.validate_dataset(data)
    contract.validate_dataset(data, require_review=False)
    data['cases'][60]['intent_group'] = 'family-0'
    with pytest.raises(ValueError, match='split_leak'):
        contract.validate_dataset(data, require_review=False)


def test_training_cli_rejects_unreviewed_before_loading_model(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(contract, 'REPOSITORY_ROOT', tmp_path)
    data = dataset()
    data['reviewed'] = False
    path = tmp_path / '.tmp' / 'draft.json'
    contract.write_new(path, data)
    def forbidden(*args, **kwargs):
        pytest.fail('Must not load E5 without reviewed labels')
    from src.intent import preparation
    monkeypatch.setattr(preparation, 'OfflineEncoder', forbidden)
    assert trainer.main(['--dataset', str(path), '--model-path', str(tmp_path / 'model'),
                         '--model-revision', 'a' * 40, '--output-prefix', str(tmp_path / '.tmp' / 'run')]) == 2
    assert json.loads(capsys.readouterr().out)['code'] == 'human_review_required'
    assert not (tmp_path / '.tmp' / 'run-candidate.json').exists()


@pytest.mark.parametrize('change', [
    lambda d: d['cases'][0].update(source='unknown'),
    lambda d: d['cases'][0].update(styles=[]),
    lambda d: d['cases'][100].update(styles=['detail']),
    lambda d: d['cases'][0].update(question=d['cases'][1]['question']),
    lambda d: d['cases'].__delitem__(0),
])
def test_invalid_dataset(change):
    data = dataset()
    change(data)
    with pytest.raises(ValueError):
        contract.validate_dataset(data)


def test_training_export_calibration_and_holdout_separation():
    data = dataset()
    cases = contract.validate_dataset(data)
    x = np.zeros((600, 384))
    for i, c in enumerate(cases):
        x[i, SOURCES.index(c['source'])] = 1
        x[i, 10:15] = [int(s in c['styles']) for s in STYLES]
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    artifact, report = trainer.train(x, cases, identity('a' * 40, 'fake', True))
    assert report['gates']['parity'] is True
    assert report['gates']['latency'] is False
    assert all(report['gates'][g] for g in GATES if g != 'latency')
    assert len(report['cases']['holdout']) == 120
    altered = copy.deepcopy(cases)
    for c in altered:
        if c['split'] == 'holdout':
            c['source'] = SOURCES[(SOURCES.index(c['source']) + 1) % len(SOURCES)]
            c['styles'] = list(STYLES) if c['source'] in ('type_a', 'mixed') else []
    second, failed = trainer.train(x, altered, artifact['identity'])
    assert second == artifact  # Holdout labels cannot change weights/thresholds.
    assert failed['gates']['source_f1'] is False


def test_paths_are_private_nonoverwriting_and_not_model_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, 'REPOSITORY_ROOT', tmp_path)
    model = tmp_path / '.tmp' / 'model'
    with pytest.raises(ValueError):
        contract.check_outputs(str(model / 'x'), ('-report.json',), model)
    with pytest.raises(ValueError):
        contract.write_new(tmp_path / 'outside.json', {})
    path = tmp_path / '.tmp' / 'test.json'
    contract.write_new(path, {})
    with pytest.raises(FileExistsError):
        contract.write_new(path, {'changed': True})


@pytest.mark.parametrize('outcome', ['accepted', 'changed', 'uncalibrated', 'quality_failed',
                                   'mutated_candidate', 'corpus_changed', 'close_failed', 'database_close_failed'])
def test_benchmark_promotes_only_reviewed_quality_and_identical_retrieval(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(contract, 'REPOSITORY_ROOT', tmp_path)
    root = tmp_path / '.tmp'
    root.mkdir()
    data = dataset()
    artifact = {'schema_version': 1, 'identity': identity('a' * 40, 'fingerprint', True),
                'source_labels': list(SOURCES), 'style_labels': list(STYLES),
                'source_weights': np.zeros((6, 384)).tolist(), 'source_bias': [0.] * 6,
                'style_weights': np.zeros((5, 384)).tolist(), 'style_bias': [0.] * 5,
                'source_threshold': .9, 'source_margin': .2, 'style_thresholds': [.5] * 5}
    report = {'dataset_sha256': digest(data), 'candidate_payload_sha256': digest(artifact),
              'gates': dict.fromkeys(GATES, True)}
    report['gates']['latency'] = False
    if outcome == 'quality_failed':
        report['gates']['source_f1'] = False
    artifact['acceptance'] = {'reviewed': True, 'dataset_sha256': digest(data),
                              'report_sha256': digest(report), 'gates': report['gates']}
    if outcome == 'mutated_candidate':
        artifact['source_bias'][0] = 99.
    for name, value in [('dataset', data), ('candidate', artifact), ('report', report)]:
        contract.write_new(root / f'{name}.json', value)
    instances = []
    class Service:
        def __init__(self, **kw):
            self._fingerprint = 'fingerprint'
            self.closed = False
            self.calls = 0
            instances.append(self)
        async def start(self):
            pass
        async def close(self):
            self.closed = True
            if outcome == 'close_failed':
                raise RuntimeError('fictional cleanup failure')
        async def retrieve(self, question, top_k):
            self.calls += 1
            mode = self._intent.settings.mode
            return {'status': 'no_matches', 'matches': ['changed'] if outcome == 'changed' and mode == 'observe' else [],
                    'fallback_reason': 'relevance_unavailable' if outcome == 'uncalibrated' else 'below_relevance_threshold',
                    'corpus_fingerprint': 'changed' if outcome == 'corpus_changed' and self.calls > 2 else 'corpus',
                    'rerank_seconds': self.calls * .01}
        def _encode(self, question):
            return [1.] + [0.] * 383
        async def _worker(self, fn, *args):
            return fn(*args)
    monkeypatch.setattr(bench, 'RetrievalService', Service)
    monkeypatch.setattr(bench.embeddings, 'model_fingerprint', lambda _: 'fingerprint')
    closed = AsyncMock()
    if outcome == 'database_close_failed':
        closed.side_effect = RuntimeError('fictional cleanup failure')
    monkeypatch.setattr(bench, 'close_database', closed)
    args = SimpleNamespace(dataset=root / 'dataset.json', candidate=root / 'candidate.json',
                           training_report=root / 'report.json', model_path=root / 'model', model_revision='a' * 40,
                           rerank=True, rerank_profile=root / 'profile.json', requests=200, output_prefix=str(root / 'result'))
    if outcome in ('quality_failed', 'mutated_candidate', 'uncalibrated', 'corpus_changed',
                   'close_failed', 'database_close_failed'):
        cleanup_failure = outcome in ('close_failed', 'database_close_failed')
        with pytest.raises(RuntimeError if cleanup_failure else ValueError):
            run_async(bench.benchmark(args))
        assert not (root / 'result-accepted.json').exists()
        assert not (root / 'result-benchmark.json').exists()
    else:
        result = run_async(bench.benchmark(args))
        assert result['status'] == ('accepted' if outcome == 'accepted' else 'gates_failed')
        assert instances[0].calls == 402
        if outcome == 'accepted':
            promoted = contract.read_json(root / 'result-accepted.json')
            validate_artifact(promoted, artifact['identity'])
        else:
            assert not (root / 'result-accepted.json').exists()
    if instances:
        assert instances[0].closed
        closed.assert_awaited_once()
