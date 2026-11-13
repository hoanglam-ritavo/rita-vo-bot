"""Transport-independent retrieval. Model work is serialized off the event loop."""
import asyncio
import math
import re

from src.ingestion import embeddings
from src.ingestion.document_packages import Rejected


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


class RetrievalService:
    def __init__(self, *, model_path, model_revision):
        if not isinstance(model_revision, str) or not re.fullmatch('[0-9a-fA-F]{40}', model_revision):
            raise RetrievalError('invalid_model_revision')
        self.model_path = model_path
        self.model_revision = model_revision.lower()
        self._model = None
        self._fingerprint = None
        self._lock = asyncio.Lock()

    async def _worker(self, function, *args):
        # Keep the lock until the worker exits even if the caller cancels.
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                pass
            raise

    def _load(self):
        fingerprint = embeddings.model_fingerprint(self.model_path)
        model = embeddings.load_model(self.model_path)
        if embeddings.model_fingerprint(self.model_path) != fingerprint:
            raise RetrievalError('model_changed')
        return model, fingerprint

    async def start(self):
        async with self._lock:
            if self._model is not None:
                return
            try:
                self._model, self._fingerprint = await self._worker(self._load)
            except RetrievalError:
                raise
            except Rejected as exc:
                raise RetrievalError(str(exc), 2 if str(exc) in {'local_model_missing', 'local_model_empty'} else 1) from None
            except Exception:
                raise RetrievalError('model_load_failed', 2) from None

    def _encode(self, question):
        model = self._model
        content = 'query: ' + question
        limit = min(model.max_seq_length, model.tokenizer.model_max_length)
        if type(limit) is not int or limit <= 0:
            raise RetrievalError('invalid_model_token_limit')
        tokens = model.tokenizer(content, add_special_tokens=True, truncation=False)['input_ids']
        if len(tokens) > limit:
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
        question = validate_request(question, top_k)
        async with self._lock:
            if self._model is None:
                raise RetrievalError('service_not_started', 2)
            try:
                vector = await self._worker(self._encode, question)
            except RetrievalError:
                raise
            except Exception:
                raise RetrievalError('query_embedding_failed', 2) from None
            fingerprint = self._fingerprint
        from .storage import search
        try:
            return await search(vector, self.model_revision, fingerprint, top_k)
        except Exception:
            raise RetrievalError('database_search_failed', 2) from None

    async def close(self):
        async with self._lock:
            self._model = None
            self._fingerprint = None
