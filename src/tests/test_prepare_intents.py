"""Offline batch CLI boundaries; synthetic data is not model acceptance evidence."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from src.intent import preparation as prep, dataset as contract
from src.intent.runtime import identity, digest, validate_artifact
from src.tests.test_intent_evaluation import dataset


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'REPOSITORY_ROOT', tmp_path)
    monkeypatch.setattr(contract, 'REPOSITORY_ROOT', tmp_path)
    private = tmp_path / '.tmp'
    private.mkdir()
    data = dataset()
    (private / 'cases.json').write_text(json.dumps(data), encoding='utf-8')
    config = {'schema_version': 1, 'dataset': '.tmp/cases.json', 'model_path': 'local-model',
              'model_revision': 'a' * 40, 'preprocessing': 'rerank_original', 'output_root': '.tmp/runs'}
    path = private / 'config.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    return path, data, config


class FakeEncoder:
    instances = []
    def __init__(self, config):
        self.config = config
        self.loads = self.encodes = self.verifies = 0
        self.closed = False
        self.instances.append(self)
    def load(self):
        self.loads += 1
    def preflight(self, cases):
        return {'status': 'passed', 'identity': self.model_identity, 'checks': []}
    @property
    def model_identity(self):
        return identity('a' * 40, 'fake', True)
    def encode(self, cases):
        self.encodes += 1
        from src.intent.runtime import SOURCES, STYLES
        x = np.zeros((len(cases), 384))
        for i, c in enumerate(cases):
            x[i, SOURCES.index(c['source'])] = 1
            x[i, 10:15] = [int(s in c['styles']) for s in STYLES]
        return x / np.linalg.norm(x, axis=1, keepdims=True)
    def verify(self):
        self.verifies += 1
    def close(self):
        self.closed = True


@pytest.fixture
def encoder(monkeypatch):
    FakeEncoder.instances = []
    monkeypatch.setattr(prep, 'OfflineEncoder', FakeEncoder)
    monkeypatch.setattr(prep, 'dependencies', lambda: {'test': 'synthetic'})
    return FakeEncoder


def test_import_and_help_do_not_import_ml_or_database():
    code = """
import sys
import src.cli.prepare_intents
assert not any(k in sys.modules for k in ('numpy', 'sklearn', 'sentence_transformers', 'onnxruntime', 'sqlalchemy', 'src.database'))
"""
    subprocess.run([sys.executable, '-c', code], check=True, capture_output=True)
    help_result = subprocess.run([sys.executable, '-m', 'src.cli.prepare_intents', '--help'], capture_output=True, text=True)
    assert help_result.returncode == 0 and 'preflight' in help_result.stdout


@pytest.mark.parametrize('update', [
    {'unexpected': True}, {'schema_version': True}, {'model_revision': 'latest'},
    {'preprocessing': 'auto'}, {'dataset': '../outside.json'}, {'output_root': 'outside'},
    {'model_path': '.tmp', 'output_root': '.tmp/runs'},
])
def test_config_rejected_without_creating_run(workspace, update):
    path, _, config = workspace
    config.update(update)
    path.write_text(json.dumps(config))
    result, code = prep.execute('run', path)
    assert code == 2 and result['status'] == 'failed'
    assert not (path.parent / 'runs').exists()


def test_aggregate_errors_without_echoing_input(workspace):
    path, data, _ = workspace
    data['cases'][0].update(question='PRIVATE QUESTION', source='PRIVATE LABEL', styles=['INVALID'])
    data['cases'][1]['critical'] = 'private'
    data['cases'][2] = None
    (path.parent / 'cases.json').write_text(json.dumps(data))
    result, code = prep.execute('validate', path)
    assert code == 1
    errors = result['validation']['errors']
    assert {e['case_index'] for e in errors if 'case_index' in e} >= {0, 1, 2}
    assert 'PRIVATE' not in json.dumps(result)


def test_draft_validate_preflight_and_run_gate(workspace, encoder, monkeypatch):
    path, data, config = workspace
    data['reviewed'] = False
    (path.parent / 'cases.json').write_text(json.dumps(data))
    elsewhere = path.parent / 'elsewhere'
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    result, code = prep.execute('validate', '.tmp/config.json')
    assert code == 0 and result['validation']['training_allowed'] is False
    assert encoder.instances == []
    result, code = prep.execute('preflight', '.tmp/config.json')
    assert code == 0 and result['preflight']['review_required'] is True
    assert encoder.instances[0].encodes == 0 and encoder.instances[0].closed
    assert not (path.parent / 'runs').exists()
    result, code = prep.execute('run', '.tmp/config.json')
    assert code == 2 and result['error_code'] == 'human_review_required'
    assert len(encoder.instances) == 1  # Review blocks before loading E5.
    manifest = json.loads((Path(result['run_directory']) / 'manifest.json').read_text())
    assert not manifest['complete'] and manifest['status'] == 'review_required'
    assert not list(Path(result['run_directory']).glob('*candidate*'))


def test_new_runs_match_legacy_and_existing_benchmark_schema(workspace, encoder, capsys):
    from scripts import train_intent as legacy
    path, data, _ = workspace
    results = [prep.execute('run', path) for _ in range(2)]
    assert all(code == 0 for _, code in results)
    runs = [Path(result['run_directory']) for result, _ in results]
    assert runs[0] != runs[1]
    assert legacy.main(['--dataset', str(path.parent / 'cases.json'), '--model-path', 'unused',
                        '--model-revision', 'a' * 40, '--rerank-preprocessing',
                        '--output-prefix', str(path.parent / 'legacy')]) == 0
    old = json.loads((path.parent / 'legacy-candidate.json').read_text())
    for run in runs:
        candidate = json.loads((run / 'candidate.json').read_text())
        report = json.loads((run / 'training-report.json').read_text())
        manifest = json.loads((run / 'manifest.json').read_text())
        assert candidate == old
        assert manifest['complete'] and manifest['status'] == 'candidate_ready_for_benchmark'
        assert manifest['outputs']['candidate.json'] == digest(candidate)
        assert candidate['acceptance']['report_sha256'] == digest(report)
        assert report['candidate_payload_sha256'] == digest({k: v for k, v in candidate.items() if k != 'acceptance'})
        validate_artifact(candidate, candidate['identity'], require_accepted=False)
        with pytest.raises(ValueError, match='not_accepted'):
            validate_artifact(candidate, candidate['identity'])
        assert not (run / 'accepted.json').exists()
    assert all(e.loads == e.encodes == 1 and e.closed for e in encoder.instances)


@pytest.mark.parametrize('failure', ['preflight', 'changed_model', 'changed_dataset', 'interrupted', 'write', 'quality', 'dependency'])
def test_failures_never_look_like_accepted_runs(workspace, encoder, monkeypatch, failure):
    path, data, _ = workspace
    if failure == 'preflight':
        monkeypatch.setattr(encoder, 'preflight', lambda *a: {'status': 'failed', 'checks': [{'case_index': 1, 'status': 'exceeded'}]})
    elif failure == 'changed_model':
        def verify(self):
            if self.encodes:
                raise ValueError('model_changed')
        monkeypatch.setattr(encoder, 'verify', verify)
    elif failure == 'changed_dataset':
        original = encoder.encode
        def encode(self, cases):
            data['review_notes'] += 'changed'
            (path.parent / 'cases.json').write_text(json.dumps(data))
            return original(self, cases)
        monkeypatch.setattr(encoder, 'encode', encode)
    elif failure == 'interrupted':
        def interrupt(*a):
            raise KeyboardInterrupt()
        monkeypatch.setattr(encoder, 'encode', interrupt)
    elif failure == 'dependency':
        def unavailable():
            raise ValueError('dependency_unavailable')
        monkeypatch.setattr(prep, 'dependencies', unavailable)
    elif failure == 'write':
        original = prep.atomic_json
        def write(path, value):
            if path.name == 'candidate.json':
                raise OSError('PRIVATE OS ERROR')
            original(path, value)
        monkeypatch.setattr(prep, 'atomic_json', write)
    elif failure == 'quality':
        original = prep.train_loaded
        def bad(*args):
            artifact, report = original(*args)
            report['gates']['source_f1'] = False
            return artifact, report
        monkeypatch.setattr(prep, 'train_loaded', bad)
    result, code = prep.execute('run', path)
    assert code == (130 if failure == 'interrupted' else 1 if failure in ('quality', 'preflight') else 2)
    manifest = json.loads((Path(result['run_directory']) / 'manifest.json').read_text())
    assert manifest['status'] != 'candidate_ready_for_benchmark'
    assert manifest['complete'] is (failure == 'quality')
    assert 'PRIVATE' not in json.dumps(result)
    assert all(e.closed for e in encoder.instances)


def test_real_encoder_token_preflight_never_encodes(workspace, monkeypatch):
    from src.ingestion import embeddings
    from unittest.mock import Mock
    from types import SimpleNamespace
    path, data, _ = workspace
    model = SimpleNamespace(max_seq_length=3, get_sentence_embedding_dimension=lambda: 384,
                            tokenizer=Mock(return_value={'input_ids': [0, 1, 2, 3]}), encode=Mock())
    model.tokenizer.model_max_length = 512
    monkeypatch.setattr(embeddings, 'load_model', lambda *a, **kw: model)
    monkeypatch.setattr(embeddings, 'model_fingerprint', lambda *a: 'fake')
    result, code = prep.execute('preflight', path)
    assert code == 1 and len(result['preflight']['checks']) == 600
    model.encode.assert_not_called()
    assert all(call.kwargs['truncation'] is False for call in model.tokenizer.call_args_list)


def test_atomic_write_preserves_previous_manifest_on_failure(tmp_path, monkeypatch):
    path = tmp_path / 'manifest.json'
    prep.atomic_json(path, {'complete': False})
    def fail(*a):
        raise OSError('disk error')
    monkeypatch.setattr(prep.os, 'replace', fail)
    with pytest.raises(OSError):
        prep.atomic_json(path, {'complete': True})
    assert json.loads(path.read_text()) == {'complete': False}
    assert list(tmp_path.iterdir()) == [path]
