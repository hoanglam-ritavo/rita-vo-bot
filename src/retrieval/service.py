"""Transport-independent retrieval. Model work is serialized off the event loop."""
import asyncio
import math
import re
import time
import logging
from dataclasses import dataclass, field

from src.ingestion import embeddings
from src.ingestion.document_packages import Rejected
from .reranking import RerankSettings, OnnxReranker, query_variants, relevance, finalize_candidates
from .semantics import SemanticSettings, SemanticExpander

logger = logging.getLogger(__name__)


@dataclass(frozen=True, repr=False)
class PreparedQuery:
    question: str
    vector: tuple[float, ...]
    owner: object = field(repr=False)


class RetrievalError(ValueError):
    def __init__(self, code, exit_code=1):
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def validate_request(question, top_k):
    if not isinstance(question, str) or not question.strip():
        raise RetrievalError('invalid_question')
    question = question.strip()
    if len(question) > 8000:
        raise RetrievalError('question_too_long')
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise RetrievalError('invalid_top_k')
    return question


def query_input(model, question):
    """Token preflight for retrieval and diagnostic callers."""
    content = 'query: ' + question
    limit = min(model.max_seq_length, model.tokenizer.model_max_length)
    if type(limit) is not int or limit <= 0:
        raise RetrievalError('invalid_model_token_limit')
    count = len(model.tokenizer(content, add_special_tokens=True, truncation=False)['input_ids'])
    return content, count, limit


class RetrievalService:
    def __init__(self, *, model_path, model_revision, device=None, rerank=None, semantic=None):
        if not isinstance(model_revision, str) or not re.fullmatch('[0-9a-fA-F]{40}', model_revision):
            raise RetrievalError('invalid_model_revision')
        self.device = device
        self.model_path = model_path
        self.model_revision = model_revision.lower()
        self._model = None
        self._fingerprint = None
        self._lock = asyncio.Lock()
        self.rerank_settings = rerank or RerankSettings()
        self._reranker = None
        self._prepared_owner = object()
        self._semantic = SemanticExpander(semantic or SemanticSettings())
        if self._semantic.settings.mode == 'expand' and not self.rerank_settings.enabled:
            raise ValueError('semantic_requires_reranker')

    @property
    def started(self):
        return self._model is not None

    async def _worker(self, function, *args):
        # Keep the lock until the worker exits even if the caller cancels.
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Repeated cancellation must not cancel the worker wrapper either.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()  # Retrieve a worker error without replacing cancellation.
            raise

    def _load(self):
        fingerprint = embeddings.model_fingerprint(self.model_path)
        model = (embeddings.load_model(self.model_path) if self.device is None
                 else embeddings.load_model(self.model_path, device=self.device))
        if embeddings.model_fingerprint(self.model_path) != fingerprint:
            raise RetrievalError('model_changed')
        return model, fingerprint

    async def start(self):
        async with self._lock:
            if self._model is not None:
                return
            try:
                try:
                    await self._worker(self._semantic.start)
                except ValueError as exc:
                    raise RetrievalError(str(exc), 2) from None
                self._model, self._fingerprint = await self._worker(self._load)
                if self.rerank_settings.enabled:
                    self._reranker = OnnxReranker(self.rerank_settings)
                    try:
                        await self._worker(self._reranker.start)
                    except BaseException:
                        self._reranker.close()
                        self._reranker = None
                        self._model = self._fingerprint = None
                        raise
                if self._reranker and self._semantic.settings.mode == 'expand':
                    self._reranker.fingerprint = self._semantic.effective_fingerprint(self._reranker.fingerprint)
            except RetrievalError:
                self._clear_runtime()
                raise
            except Rejected as exc:
                self._clear_runtime()
                raise RetrievalError(str(exc), 2 if str(exc) in {'local_model_missing', 'local_model_empty'} else 1) from None
            except Exception:
                self._clear_runtime()
                raise RetrievalError('model_load_failed', 2) from None
            except BaseException:
                self._clear_runtime()
                raise

    def _encode(self, question):
        model = self._model
        content, count, limit = query_input(model, question)
        if count > limit:
            raise RetrievalError('question_exceeds_model_limit')
        vectors = model.encode([content], normalize_embeddings=True, convert_to_numpy=True,
                               show_progress_bar=False)
        if len(vectors) != 1:
            raise RetrievalError('invalid_query_vector')
        vector = vectors[0].tolist()
        try:
            embeddings.validate_vectors([{'chunk_index': 0, 'embedding': vector}])
        except Rejected:
            raise RetrievalError('invalid_query_vector') from None
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0:
            raise RetrievalError('invalid_query_vector')
        return [value / norm for value in vector]

    async def retrieve(self, question, top_k=5):
        return await self._retrieve(question, top_k, assessment=True)

    async def read_evidence(self, references):
        async with self._lock:
            if self._model is None:
                raise RetrievalError('service_not_started', 2)
            from .storage import read_references
            try:
                matches = await read_references(references, self.model_revision, self._fingerprint)
            except Exception:
                raise RetrievalError('database_evidence_failed', 2) from None
            return {'status': 'matches_found' if matches else 'no_matches', 'matches': matches,
                    'fallback_reason': None if matches else 'no_candidates',
                    'relevance': relevance(matches), 'rerank_seconds': 0.0}

    async def prepare_query(self, question, top_k=5):
        question = validate_request(question, top_k)
        async with self._lock:
            if self._model is None:
                raise RetrievalError('service_not_started', 2)
            original = query_variants(question)[0] if self._reranker else question
            try:
                vector = await self._worker(self._encode, original)
                return PreparedQuery(question, tuple(vector), self._prepared_owner)
            except RetrievalError:
                raise
            except Exception:
                raise RetrievalError('query_embedding_failed', 2) from None

    async def retrieve_prepared(self, prepared, top_k=5):
        return await self._retrieve(prepared.question, top_k, assessment=True, prepared=prepared)

    async def _retrieve(self, question, top_k=5, *, assessment, prepared=None):
        # Offline assessment=False exposes candidate diagnostics; public output always uses finalization.
        question = validate_request(question, top_k)
        
        async with self._lock:
            if self._model is None:
                raise RetrievalError('service_not_started', 2)
            try:
                variants = query_variants(question) if self._reranker else [question]
                rerank_question = variants[-1]
                if prepared is not None:
                    if not isinstance(prepared, PreparedQuery) or prepared.owner is not self._prepared_owner or prepared.question != question:
                        raise RetrievalError('invalid_prepared_query', 2)
                    vector = list(prepared.vector)
                else:
                    vector = await self._worker(self._encode, variants[0])
                skipped = 0
                for variant in self._semantic.expansion(rerank_question):
                    if variant in variants:
                        continue
                    # Do not truncate a rewrite or silently lose a condition to fit it.
                    _, count, limit = query_input(self._model, variant)
                    if count > limit:
                        skipped += 1
                        continue
                    variants.append(variant)
                extra = [await self._worker(self._encode, variant) for variant in variants[1:]]
                if self._semantic.settings.mode == 'expand':
                    logger.info('semantic_expansion variants=%d skipped=%d', len(variants), skipped)
            except RetrievalError:
                raise
            except Exception:
                raise RetrievalError('query_embedding_failed', 2) from None
            from .storage import search
            try:
                count = 30 if self._reranker else top_k
                kwargs = {'extra_vectors': extra} if extra else {}
                result = await search(vector, self.model_revision, self._fingerprint, count, **kwargs)
            except Exception:
                raise RetrievalError('database_search_failed', 2) from None
            result['top_k'] = top_k
            result['rerank_seconds'] = 0.0
            candidates = result['matches']
            for match in candidates:
                match.setdefault('rerank_score', None)
            # IDs and ranks only; evaluator can inspect recall without retaining text.
            result['candidate_order'] = [{'chunk_id': m['chunk_id'], 'chunk_index': m['chunk_index'],
                'revision_id': m['revision_id'], 'run_id': m['run_id']} for m in candidates]
            if self._reranker and candidates:
                started = time.perf_counter()
                try:
                    scores, windows = await self._worker(self._reranker.score, rerank_question, candidates)
                except ValueError as exc:
                    if str(exc) == 'reranker_question_exceeds_model_limit':
                        raise RetrievalError('question_exceeds_model_limit') from None
                    raise RetrievalError('reranking_failed', 2) from None
                except Exception:
                    raise RetrievalError('reranking_failed', 2) from None
                for match, score in zip(candidates, scores):
                    match['rerank_score'] = score
                candidates.sort(key=lambda m: (-m['rerank_score'], m['cosine_distance'], m['chunk_id']))
                result['rerank_seconds'] = time.perf_counter() - started
                logger.info('reranking_finished candidates=%d windows=%d seconds=%.3f',
                            len(candidates), windows, result['rerank_seconds'])
            result['reranker_fingerprint'] = self._reranker.fingerprint if self._reranker else None
            if assessment:
                finalize_candidates(result, top_k)
                result.pop('candidate_order', None)
                logger.info('retrieval_finished status=%s reason=%s candidates=%d retained=%d',
                            result['status'], result['fallback_reason'], len(candidates), len(result['matches']))
            else:
                result['matches'] = candidates
                result['relevance'] = relevance(candidates)
            result['notice'] = 'Unassessed source candidates; not an answer or completeness guarantee.'
            return result

    def _clear_runtime(self):
        self._prepared_owner = object()
        self._semantic.close()
        if self._reranker:
            self._reranker.close()
            self._reranker = None
        self._model = None
        self._fingerprint = None

    async def close(self):
        async with self._lock:
            self._clear_runtime()
