"""Batch preparation orchestration. Imports never load ML runtimes or a database."""
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import re
import uuid

from src.paths import REPOSITORY_ROOT
from .dataset import private_path, read_json, inspect_dataset, dataset_hash, safe_code
from .runtime import identity, digest, validate_artifact

CONFIG_FIELDS = {'schema_version', 'dataset', 'model_path', 'model_revision', 'preprocessing', 'output_root'}
ERRORS = {'invalid_config', 'output_overlaps_model', 'dependency_unavailable', 'model_dimensions',
          'local_model_missing', 'local_model_empty', 'invalid_model_token_limit', 'model_changed',
          'training_inputs_changed', 'preflight_failed'}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def resolve_path(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('invalid_config')
    path = Path(value)
    return (path if path.is_absolute() else REPOSITORY_ROOT / path).resolve()


def load_config(path):
    config_path = private_path(resolve_path(str(path)))
    config = read_json(config_path)
    if (not isinstance(config, dict) or set(config) != CONFIG_FIELDS
            or type(config['schema_version']) is not int or config['schema_version'] != 1
            or not isinstance(config['model_revision'], str)
            or not re.fullmatch('[a-fA-F0-9]{40}', config['model_revision'])
            or config['preprocessing'] not in ('rerank_original', 'vector_only')):
        raise ValueError('invalid_config')
    result = dict(config)
    for key in ('dataset', 'model_path', 'output_root'):
        path = resolve_path(config[key])
        if key != 'model_path':
            private_path(path)
        result[key] = str(path)
    result['model_revision'] = config['model_revision'].lower()
    model, output = Path(result['model_path']), Path(result['output_root'])
    if output.is_relative_to(model):
        raise ValueError('output_overlaps_model')
    return result


def atomic_json(path, value):
    """Replace only a file in this run's exclusively owned directory."""
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def dependencies():
    try:
        return {name: version(name) for name in ('sentence-transformers', 'numpy', 'scikit-learn')}
    except PackageNotFoundError:
        raise ValueError('dependency_unavailable') from None


class OfflineEncoder:
    """Reuse retrieval's exact load, token and encode implementation without start/search."""
    def __init__(self, config):
        from src.retrieval.service import RetrievalService
        self.config = config
        self.runtime = RetrievalService(model_path=config['model_path'], model_revision=config['model_revision'], device='cpu')

    def load(self):
        self.runtime._model, self.runtime._fingerprint = self.runtime._load()
        if self.runtime._model.get_sentence_embedding_dimension() != 384:
            raise ValueError('model_dimensions')

    def question(self, case):
        from src.retrieval.service import validate_request
        from src.retrieval.reranking import query_variants
        text = validate_request(case['question'], 5)
        return query_variants(text)[0] if self.config['preprocessing'] == 'rerank_original' else text

    def preflight(self, cases):
        from src.retrieval.service import query_input
        checks = []
        for index, case in enumerate(cases):
            _, count, limit = query_input(self.runtime._model, self.question(case))
            checks.append({'case_index': index, 'token_count': count, 'limit': limit,
                           'status': 'passed' if count <= limit else 'exceeded'})
        return {'status': 'passed' if all(c['status'] == 'passed' for c in checks) else 'failed',
                'identity': self.model_identity, 'checks': checks}

    @property
    def model_identity(self):
        return identity(self.config['model_revision'], self.runtime._fingerprint,
                        self.config['preprocessing'] == 'rerank_original')

    def encode(self, cases):
        import numpy as np
        return np.asarray([self.runtime._encode(self.question(case)) for case in cases])

    def verify(self):
        from src.ingestion import embeddings
        if embeddings.model_fingerprint(self.config['model_path']) != self.runtime._fingerprint:
            raise ValueError('model_changed')

    def close(self):
        self.runtime._model = self.runtime._fingerprint = None


def train_loaded(encoder, data):
    """Shared by batch CLI and legacy trainer; returns the existing artifact schema."""
    from .training import train
    artifact, report = train(encoder.encode(data['cases']), data['cases'], encoder.model_identity)
    report['dataset_sha256'] = dataset_hash(data)
    report['candidate_payload_sha256'] = digest(artifact)
    artifact['acceptance'] = {'reviewed': True, 'dataset_sha256': dataset_hash(data),
                              'report_sha256': digest(report), 'gates': report['gates']}
    validate_artifact(artifact, artifact['identity'], require_accepted=False)
    return artifact, report


def execute(command, config_path, progress=lambda stage: None):
    run_dir = manifest = encoder = None
    try:
        if command not in ('validate', 'preflight', 'run'):
            raise ValueError('invalid_config')
        config = load_config(config_path)
        if command == 'run':
            root = Path(config['output_root'])
            root.mkdir(parents=True, exist_ok=True)
            proposed = root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex)
            proposed.mkdir(exist_ok=False)
            run_dir = proposed
            manifest = {'schema_version': 1, 'status': 'running', 'complete': False,
                        'started_at': timestamp(), 'config': config, 'tool_version': 'intent-preparation-v1', 'outputs': {}}
            atomic_json(run_dir / 'manifest.json', manifest)

        def finish(status, code, **fields):
            nonlocal encoder
            if encoder is not None:
                closing, encoder = encoder, None
                closing.close()
            result = {'status': status, **fields}
            if manifest is not None:
                manifest.update(status=status, complete=status in ('candidate_ready_for_benchmark', 'quality_failed'),
                                finished_at=timestamp(), exit_code=code, **fields)
                atomic_json(run_dir / 'manifest.json', manifest)
                result['run_directory'] = str(run_dir)
            return result, code

        progress('validation')
        data = read_json(config['dataset'])
        validation = inspect_dataset(data)
        if run_dir is not None:
            atomic_json(run_dir / 'validation.json', validation)
            manifest['dataset_sha256'] = dataset_hash(data)
        if validation['errors']:
            return finish('validation_failed', 1, validation=validation)
        if command == 'validate':
            return finish('validated', 0, validation=validation)
        if command == 'run' and not validation['training_allowed']:
            return finish('review_required', 2, validation=validation, error_code='human_review_required')
        progress('model_preflight')
        versions = dependencies()
        encoder = OfflineEncoder(config)
        encoder.load()
        preflight = encoder.preflight(data['cases'])
        encoder.verify()
        if dataset_hash(read_json(config['dataset'])) != dataset_hash(data):
            raise ValueError('training_inputs_changed')
        preflight.update(dependencies=versions, review_required=validation['review_required'],
                         training_allowed=validation['training_allowed'] and preflight['status'] == 'passed')
        if run_dir is not None:
            atomic_json(run_dir / 'preflight.json', preflight)
            manifest.update(model_identity=encoder.model_identity, dependencies=versions)
        if preflight['status'] != 'passed':
            return finish('preflight_failed', 1, preflight=preflight)
        if command == 'preflight':
            return finish('preflight_passed', 0, preflight=preflight)
        progress('embedding_training_evaluation')
        artifact, report = train_loaded(encoder, data)
        encoder.verify()
        if dataset_hash(read_json(config['dataset'])) != dataset_hash(data):
            raise ValueError('training_inputs_changed')
        encoder.close()
        encoder = None
        # Manifest becomes complete only after both outputs have been written atomically.
        atomic_json(run_dir / 'training-report.json', report)
        atomic_json(run_dir / 'candidate.json', artifact)
        manifest['outputs'] = {name: digest(value) for name, value in [('training-report.json', report), ('candidate.json', artifact)]}
        passed = all(value for key, value in report['gates'].items() if key != 'latency')
        if passed:
            manifest['benchmark_arguments'] = [
                'python', '-m', 'scripts.benchmark_intent', '--dataset', config['dataset'],
                '--candidate', str(run_dir / 'candidate.json'), '--training-report', str(run_dir / 'training-report.json'),
                '--model-path', config['model_path'], '--model-revision', config['model_revision'],
                '--rerank', '--rerank-profile', '<matching-profile-path>', '--output-prefix', '<new-private-output-prefix>']
            manifest['benchmark_requires'] = ['calibrated_retrieval', 'operator_supplied_profile', 'rerank_original_preprocessing']
        return finish('candidate_ready_for_benchmark' if passed else 'quality_failed', 0 if passed else 1, gates=report['gates'])
    except BaseException as exc:
        if not isinstance(exc, (Exception, KeyboardInterrupt)):
            raise
        interrupted = isinstance(exc, KeyboardInterrupt)
        code = 'interrupted' if interrupted else str(exc) if str(exc) in ERRORS else safe_code(exc, 'intent_preparation_failed')
        result = {'status': 'interrupted' if interrupted else 'failed', 'error_code': code}
        if run_dir is not None:
            result['run_directory'] = str(run_dir)
            if manifest is None:
                manifest = {'schema_version': 1, 'complete': False}
            manifest.update(status=result['status'], complete=False, finished_at=timestamp(), error_code=code,
                            exit_code=130 if interrupted else 2)
            try:
                atomic_json(run_dir / 'manifest.json', manifest)
            except OSError:
                # The initial incomplete manifest is still not an accepted completion.
                result['manifest_update_failed'] = True
        return result, 130 if interrupted else 2
    finally:
        if encoder is not None:
            try:
                encoder.close()
            except Exception:
                # Failure paths already have an incomplete manifest; never replace
                # their sanitized error with a dependency traceback during cleanup.
                pass
