"""Application-owned retrieval lifecycle and bounded HTTP request execution."""
import asyncio
import logging
import time

from src.retrieval import RetrievalService, RetrievalError
from .contracts import ApiError, RetrievalResponse, Timings

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 120


class ApiService:
    def __init__(self, settings, *, retrieval=None, database_check=None, database_close=None):
        from src.database import check_database, close_database
        self.settings = settings
        self.retrieval = retrieval if retrieval is not None else RetrievalService(
            model_path=settings.model_path, model_revision=settings.model_revision, device='cpu',
            rerank=settings.rerank, intent=settings.intent,
            relevance_filter_enabled=settings.relevance_filter_enabled)
        self.database_check = database_check or check_database
        self.database_close = database_close or close_database
        self.started = False
        self.busy = False
        self._idle = asyncio.Event()
        self._idle.set()

    async def start(self):
        if self.started:
            return
        started = time.perf_counter()
        await self.retrieval.start()
        model_seconds = time.perf_counter() - started
        await self.database_check()
        self.started = True
        logger.info('retrieval_startup model_initialization_seconds=%.3f total_seconds=%.3f',
                    model_seconds, time.perf_counter() - started)

    async def ready(self):
        if not self.started or not self.retrieval.started:
            raise ApiError('not_ready')
        try:
            async with asyncio.timeout(15):
                await self.database_check()
        except Exception:
            raise ApiError('not_ready') from None

    async def close(self):
        self.started = False
        await self._idle.wait()
        try:
            await self.retrieval.close()
        finally:
            await self.database_close()

    async def retrieve(self, text, top_k=None):
        if not self.started:
            raise ApiError('not_ready')
        # No await between checking and taking ownership on the owning loop.
        if self.busy:
            raise ApiError('busy', 429)
        self.busy = True
        self._idle.clear()
        started = time.perf_counter()
        outcome = 'failed'
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                retrieval_started = time.perf_counter()
                result = await self.retrieval.retrieve(text, self.settings.top_k if top_k is None else top_k)
                retrieval_seconds = time.perf_counter() - retrieval_started
                if result['status'] == 'model_mismatch':
                    raise ApiError('model_mismatch')
                response = RetrievalResponse(status=result['status'], matches=result['matches'],
                    fallback_reason=result['fallback_reason'],
                    relevance=result.get('relevance', {'status': 'unassessed', 'warnings': ['evidence_completeness_not_assessed']}),
                    timings=Timings(retrieval_seconds=retrieval_seconds,
                                    rerank_seconds=result.get('rerank_seconds', 0.0),
                                    total_seconds=time.perf_counter() - started))
                outcome = response.status
                return response
        except RetrievalError as exc:
            invalid = exc.code in {'invalid_question', 'question_too_long', 'question_exceeds_model_limit', 'invalid_top_k'}
            raise ApiError(exc.code, 422 if invalid else 503) from None
        except TimeoutError:
            raise ApiError('retrieval_timeout', 504) from None
        except ApiError:
            raise
        except Exception:
            raise ApiError('retrieval_failed') from None
        finally:
            self.busy = False
            self._idle.set()
            logger.info('retrieval_finished status=%s seconds=%.3f', outcome, time.perf_counter() - started)
