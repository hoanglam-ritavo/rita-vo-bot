"""Local CPU reranking; no database, network, or import-time model loading."""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata

from src.paths import REPOSITORY_ROOT

MODEL_ID = 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'
MODEL_REVISION = '1427fd652930e4ba29e8149678df786c240d8825'
MODEL_FILE = 'model_quint8_avx2.onnx'
# Official pinned artifacts; the local model filename is deliberately flattened.
ARTIFACTS = {
    MODEL_FILE: '6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b',
    'config.json': 'cc2cfe51aa3fd759d21d21acf5dfd6994aa67a3c9210636d22e143699d336c77',
    'tokenizer.json': '62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626',
    'tokenizer_config.json': 'e7fbfbfa6347b4e414c1cee50d142e2c2f9a895dad68b068ae83a8b564c3837e',
    'special_tokens_map.json': '378eb3bf733eb16e65792d7e3fda5b8a4631387ca04d2015199c4d4f22ae554d',
    'sentencepiece.bpe.model': 'cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865',
}
PIPELINE_VERSION = 'nfc-typos2-rrf60-window512-overlap64-max-filter-v2'
WARNING = 'evidence_completeness_not_assessed'


@dataclass(frozen=True)
class RerankSettings:
    enabled: bool = False
    model_path: Path = REPOSITORY_ROOT / 'models'
    model_file: str = MODEL_FILE
    candidate_count: int = 30
    batch_size: int = 4
    profile_path: Path | None = None

    def __post_init__(self):
        if type(self.enabled) is not bool or self.model_file != MODEL_FILE:
            raise ValueError('invalid_reranker_configuration')
        if type(self.candidate_count) is not int or self.candidate_count != 30:
            raise ValueError('invalid_candidate_count')
        if type(self.batch_size) is not int or not 1 <= self.batch_size <= 16:
            raise ValueError('invalid_reranker_batch_size')
        for name in ('model_path', 'profile_path'):
            value = getattr(self, name)
            if value is not None:
                value = Path(value)
                object.__setattr__(self, name, value if value.is_absolute() else REPOSITORY_ROOT / value)


def query_variants(question):
    original = ' '.join(unicodedata.normalize('NFC', question).split())
    corrected = original
    for wrong, right in (('thai sảng', 'thai sản'), ('nguyên đáng', 'nguyên đán')):
        corrected = re.sub(r'(?<!\w)' + wrong + r'(?!\w)', right, corrected, flags=re.IGNORECASE)
    return [original] if corrected == original else [original, corrected]


def fuse_candidates(lists, limit):
    records, scores = {}, {}
    for matches in lists:
        seen = set()
        for rank, match in enumerate(matches, 1):
            key = match['chunk_id']
            if key in seen:
                continue
            seen.add(key)
            records[key] = match
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
    return [records[key] for key in sorted(records, key=lambda key: (-scores[key], key))[:limit]]


def relevance(matches, threshold=None):
    if not matches:
        return {'status': 'no_candidates', 'warnings': [WARNING]}
    if threshold is None:
        return {'status': 'unassessed', 'warnings': [WARNING]}
    low = max(m['rerank_score'] for m in matches) < threshold
    return {'status': 'low_relevance' if low else 'relevant_candidates',
            'warnings': [WARNING, 'low_relevance'] if low else [WARNING]}


def load_profile(path):
    if path is None or not path.exists():
        return None
    try:
        profile = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(profile, dict):
            raise ValueError()
        if (type(profile.get('threshold')) not in (float, int)
                or not math.isfinite(profile['threshold'])
                or type(profile.get('holdout_passed')) is not bool
                or any(not isinstance(profile.get(k), str) or not profile[k]
                       for k in ('fingerprint',))):
            raise ValueError()
        version = profile.get('schema_version', 1)
        if type(version) is not int:
            raise ValueError()
        # Legacy top-1 profiles cannot certify individual passages.
        if version != 2:
            return None
        if not isinstance(profile.get('corpus_fingerprint'), str) or not profile['corpus_fingerprint']:
            raise ValueError()
        return profile
    except (ValueError, TypeError, OSError):
        raise ValueError('invalid_relevance_profile') from None


def profile_threshold(profile, fingerprint, corpus_fingerprint):
    if (profile and profile['holdout_passed'] is True
            and profile['fingerprint'] == fingerprint
            and corpus_fingerprint is not None
            and profile['corpus_fingerprint'] == corpus_fingerprint):
        return float(profile['threshold'])
    return None


def filter_candidates(result, threshold, top_k, *, enabled=True):
    """Withhold unassessed candidates unless filtering is explicitly disabled."""
    candidates = result['matches']
    result['relevance'] = relevance(candidates, threshold if enabled else None)
    if not enabled:
        result['relevance']['warnings'].append('relevance_filter_disabled')
    if result['status'] == 'model_mismatch':
        result['fallback_reason'] = None
        return result
    if result['status'] == 'no_eligible_documents':
        reason = 'no_eligible_documents'
    elif not candidates:
        reason = 'no_candidates'
    elif not enabled:
        result['matches'] = candidates[:top_k]
        reason = None
    elif threshold is None:
        reason = 'relevance_unavailable'
    else:
        result['matches'] = [m for m in candidates if m['rerank_score'] >= threshold][:top_k]
        reason = None if result['matches'] else 'below_relevance_threshold'
    if reason:
        result['matches'] = []
    result['fallback_reason'] = reason
    if result['status'] != 'no_eligible_documents':
        result['status'] = 'matches_found' if result['matches'] else 'no_matches'
    return result


class OnnxReranker:
    def __init__(self, settings):
        self.settings = settings
        self.session = self.tokenizer = None
        self.profile = None
        self.fingerprint = None

    def start(self):
        from numpy._core._multiarray_umath import __cpu_features__
        import onnxruntime as ort
        from transformers import AutoTokenizer
        if not __cpu_features__.get('AVX2', False):
            raise ValueError('reranker_requires_avx2')
        for name, expected in ARTIFACTS.items():
            with (self.settings.model_path / name).open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError('reranker_artifact_mismatch')
        identity = {'artifacts': ARTIFACTS, 'revision': MODEL_REVISION,
                    'pipeline': PIPELINE_VERSION, 'candidate_count': self.settings.candidate_count,
                    'batch_size': self.settings.batch_size, 'onnxruntime': ort.__version__}
        self.fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.settings.model_path, local_files_only=True, trust_remote_code=False)
        if (self.tokenizer.bos_token_id, self.tokenizer.eos_token_id,
                self.tokenizer.num_special_tokens_to_add(pair=True)) != (0, 2, 4):
            raise ValueError('invalid_reranker_tokenizer')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(self.settings.model_path / self.settings.model_file),
            sess_options=options, providers=['CPUExecutionProvider'])
        self.score('test', [{'content': 'test'}])  # Validate graph IO before readiness.
        self.profile = load_profile(self.settings.profile_path)

    def score(self, question, matches):
        import numpy as np
        query = self.tokenizer.encode(question, add_special_tokens=False)
        capacity = 512 - len(query) - self.tokenizer.num_special_tokens_to_add(pair=True)
        if capacity <= 64:
            raise ValueError('reranker_question_exceeds_model_limit')
        scores = [-math.inf] * len(matches)
        batch, owners = [], []
        windows = 0

        def flush():
            if not batch:
                return
            encoded = self.tokenizer.pad(batch, padding=True, return_tensors='np')
            inputs = {item.name: np.asarray(encoded[item.name], dtype=np.int64)
                      for item in self.session.get_inputs()}
            result = np.asarray(self.session.run(None, inputs)[0])
            if result.shape not in ((len(batch), 1), (len(batch),)) or not np.isfinite(result).all():
                raise ValueError('invalid_reranker_scores')
            for owner, score in zip(owners, result.reshape(-1)):
                scores[owner] = max(scores[owner], float(score))
            batch.clear()
            owners.clear()

        for owner, match in enumerate(matches):
            tokens = self.tokenizer.encode(match['content'], add_special_tokens=False)
            for start in range(0, max(1, len(tokens)), capacity - 64):
                window = tokens[start:start + capacity]
                # Pinned XLM-R pair format: <s> query </s></s> passage </s>.
                # Assemble token IDs directly to preserve every window token.
                ids = [0] + query + [2, 2] + window + [2]
                batch.append({'input_ids': ids, 'attention_mask': [1] * len(ids)})
                owners.append(owner)
                windows += 1
                if len(batch) == self.settings.batch_size:
                    flush()
                if start + capacity >= len(tokens):
                    break
        flush()
        return scores, windows

    def close(self):
        self.session = self.tokenizer = None
        self.profile = self.fingerprint = None
