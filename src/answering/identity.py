"""Reproducible model, code and pipeline identity for reviewed answer studies."""
from dataclasses import asdict
import hashlib
import json
from importlib import metadata, util
import os
from pathlib import Path
import sys

from src.paths import REPOSITORY_ROOT

MODEL_ID = 'Qwen3.5-0.8B-Q5_K_M'
DEADLINE_SECONDS = 30
BINDING_VERSION = '0.3.37'


def binding_identity():
    """Inspect installed binding/native bytes without importing or loading a model."""
    if os.environ.get('LLAMA_CPP_LIB_PATH'):
        raise ValueError('answer_library_override_not_supported')
    try:
        version = metadata.version('llama-cpp-python')
        spec = util.find_spec('llama_cpp')
        if version != BINDING_VERSION or spec is None or not spec.origin:
            raise ValueError('answer_binding_missing_or_incompatible')
        root = Path(spec.origin).resolve().parent
        files = sorted(p for p in root.rglob('*') if p.is_file()
                       and p.suffix.lower() in {'.py', '.dll', '.pyd', '.so', '.dylib'})
        if not any(p.suffix.lower() in {'.dll', '.so', '.dylib'} for p in files):
            raise ValueError('answer_native_library_missing')
        digest = hashlib.sha256(json.dumps({p.relative_to(root).as_posix(): sha_file(p)
            for p in files}, sort_keys=True).encode()).hexdigest()
        return {'backend': 'llama-cpp-python-inprocess', 'binding_version': version,
                'python': sys.version.split()[0], 'abi': sys.implementation.cache_tag,
                'jinja2': metadata.version('jinja2'), 'numpy': metadata.version('numpy'),
                'binding_sha256': digest}
    except metadata.PackageNotFoundError:
        raise ValueError('answer_binding_missing_or_incompatible') from None


def sha_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def implementation_identity():
    files = sorted(p for folder in ('answering', 'retrieval', 'intent', 'api')
                   for p in (REPOSITORY_ROOT / 'src' / folder).rglob('*.py'))
    return hashlib.sha256(json.dumps({str(p.relative_to(REPOSITORY_ROOT)): sha_file(p)
                                     for p in files}, sort_keys=True).encode()).hexdigest()


def retrieval_identity(settings):
    config = {name: getattr(settings, name) for name in (
        'model_revision', 'rerank', 'semantic', 'intent', 'relevance_filter_enabled')}
    config = {k: asdict(v) if hasattr(v, '__dataclass_fields__') else v for k, v in config.items()}
    artifacts = {}
    for group in ('rerank', 'semantic', 'intent'):
        for key, value in config[group].items():
            if key.endswith('_path') and value is not None and Path(value).is_file():
                artifacts[f'{group}.{key}'] = sha_file(value)
    return hashlib.sha256(json.dumps({'settings': config, 'artifacts': artifacts},
                                    sort_keys=True, default=str).encode()).hexdigest()
