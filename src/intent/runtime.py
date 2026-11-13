"""Small linear heads over the existing E5 query vector. Never controls retrieval."""
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
import time

from src.paths import REPOSITORY_ROOT

SOURCES = ('type_a', 'type_b', 'mixed', 'conversation', 'out_of_scope', 'needs_context')
STYLES = ('detail', 'conditions', 'procedure', 'enumeration_count', 'comparison')
GATES = ('source_f1', 'accepted_precision', 'coverage', 'style_f1', 'critical', 'parity', 'latency')
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntentSettings:
    mode: str = 'off'
    artifact_path: Path | None = None

    def __post_init__(self):
        if self.mode not in ('off', 'observe'):
            raise ValueError('invalid_intent_mode')
        if self.artifact_path is not None:
            path = Path(self.artifact_path)
            object.__setattr__(self, 'artifact_path', path if path.is_absolute() else REPOSITORY_ROOT / path)


def identity(revision, fingerprint, rerank_enabled):
    return {'model': 'intfloat/multilingual-e5-small', 'revision': revision.lower(),
            'files_sha256': fingerprint, 'dimensions': 384, 'normalize': True,
            'prefix': 'query: ', 'preprocessing': 'strip-nfc-whitespace-v1' if rerank_enabled else 'strip-v1'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def validate_artifact(data, expected_identity, *, require_accepted=True):
    import numpy as np
    if (not isinstance(data, dict) or type(data.get('schema_version')) is not int or data.get('schema_version') != 1
            or data.get('identity') != expected_identity
            or data.get('source_labels') != list(SOURCES) or data.get('style_labels') != list(STYLES)):
        raise ValueError('intent_artifact_incompatible')
    acceptance = data.get('acceptance', {})
    if require_accepted and (acceptance.get('reviewed') is not True
            or any(acceptance.get('gates', {}).get(gate) is not True for gate in GATES)
            or any(not isinstance(acceptance.get(key), str) or len(acceptance[key]) != 64
                   or any(c not in '0123456789abcdef' for c in acceptance[key])
                   for key in ('dataset_sha256', 'report_sha256'))):
        raise ValueError('intent_artifact_not_accepted')
    for key, shape in [('source_weights', (6, 384)), ('source_bias', (6,)),
                       ('style_weights', (5, 384)), ('style_bias', (5,)), ('style_thresholds', (5,))]:
        value = np.asarray(data.get(key))
        if value.shape != shape or value.dtype.kind not in 'iuf' or not np.isfinite(value).all():
            raise ValueError('invalid_intent_weights')
    for value in [data.get('source_threshold'), data.get('source_margin'), *data['style_thresholds']]:
        if type(value) not in (int, float) or not 0 <= value <= 1:
            raise ValueError('invalid_intent_threshold')
    return data


def scores(data, vectors):
    """Batch prediction, shared by offline evaluation and runtime (no sklearn import)."""
    import numpy as np
    x = np.asarray(vectors, dtype=float)
    if x.ndim != 2 or x.shape[1] != 384 or not np.isfinite(x).all():
        raise ValueError('invalid_intent_vector')
    logits = x @ np.asarray(data['source_weights']).T + data['source_bias']
    exponent = np.exp(logits - logits.max(axis=1, keepdims=True))
    source = exponent / exponent.sum(axis=1, keepdims=True)
    style_logits = x @ np.asarray(data['style_weights']).T + data['style_bias']
    style = np.exp(-np.logaddexp(0, -style_logits))
    if not np.isfinite(source).all() or not np.isfinite(style).all():
        raise ValueError('invalid_intent_scores')
    return source, style


def classify(data, vector):
    source_scores, style_scores = scores(data, [vector])
    ranked = source_scores[0].argsort()[::-1]
    first, second = ranked[:2]
    if (source_scores[0, first] < data['source_threshold']
            or source_scores[0, first] - source_scores[0, second] < data['source_margin']):
        return {'status': 'uncertain', 'source': None, 'styles': []}
    source = SOURCES[first]
    styles = [name for name, score, threshold in zip(STYLES, style_scores[0], data['style_thresholds'])
              if score >= threshold] if source in ('type_a', 'mixed') else []
    return {'status': 'classified', 'source': source, 'styles': styles}


class IntentObserver:
    def __init__(self, settings):
        self.settings = settings
        self.artifact = None
        self.version = 'none'

    def start(self, expected_identity):
        if self.settings.mode == 'off':
            return
        try:
            path = self.settings.artifact_path
            if path is None or path.stat().st_size > 1_000_000:
                raise ValueError('invalid_artifact_path')
            data = json.loads(path.read_text(encoding='utf-8'))
            self.artifact = validate_artifact(data, expected_identity)
            self.version = digest(data)
        except Exception:
            self.close()
            logger.warning('intent_unavailable code=artifact_unavailable')

    def observe(self, vector):
        if self.settings.mode == 'off':
            return
        started = time.perf_counter()
        result = {'status': 'unavailable', 'source': None, 'styles': []}
        if self.artifact is not None:
            try:
                result = classify(self.artifact, vector)
            except Exception:
                logger.warning('intent_unavailable code=inference_failed')
        logger.info('intent_observed status=%s source=%s styles=%s artifact=%s seconds=%.6f',
                    result['status'], result['source'], ','.join(result['styles']), self.version,
                    time.perf_counter() - started)

    def close(self):
        self.artifact = None
        self.version = 'none'
