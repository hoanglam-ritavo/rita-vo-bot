"""Template answer lifecycle sharing retrieval ownership; no GGUF dependencies."""
import asyncio
import logging
import time
from uuid import uuid4
from src.answering.catalog import load_catalog
from src.answering.pipeline import evaluate_question
from src.retrieval import RetrievalError
from .contracts import AnswerResponse, AnswerTimings, ApiError

logger = logging.getLogger(__name__)
DEADLINE_SECONDS = 120


async def drain(task):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


class AnswerService:
    def __init__(self, settings, owner):
        self.settings, self.owner = settings, owner
        self.available = False
        self.catalog = self.lexicon = None

    async def start(self):
        if not self.settings.answer.enabled:
            return
        try:
            if self.settings.semantic.mode != 'expand':
                raise ValueError('answer_semantics_required')
            task = asyncio.create_task(asyncio.to_thread(load_catalog,
                self.settings.answer.catalog_path, self.settings.semantic.artifact_path))
            try:
                self.catalog, self.lexicon = await asyncio.shield(task)
            except asyncio.CancelledError:
                try:
                    await drain(task)
                except Exception:
                    pass  # Preserve cancellation even if the owned file-read worker failed.
                raise
            self.available = True
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception:
            await self.close()
            logger.warning('answer_unavailable code=answer_catalog_unavailable')

    async def close(self):
        self.available = False
        self.catalog = self.lexicon = None

    async def answer(self, text, top_k):
        if not self.owner.started or not self.available:
            raise ApiError('answer_unavailable')
        if self.owner.busy:
            raise ApiError('busy', 429)
        self.owner.busy = True
        self.owner._idle.clear()
        started, request_id, outcome = time.perf_counter(), uuid4().hex, 'failed'
        try:
            async with asyncio.timeout(DEADLINE_SECONDS):
                row = await evaluate_question(text, self.owner.retrieval, self.catalog, self.lexicon,
                    top_k=top_k, allow_unassessed=not self.settings.relevance_filter_enabled)
            referenced = {c['source_id'] for claim in row['claims'] for c in claim['citations']}
            sources = [dict(source_id=key, **{k: v for k, v in source.items() if k != 'content'})
                       for key, source in row['sources'].items() if key in referenced]
            lines = ([row['intro']] if row['intro'] else []) + [
                claim['text'] + ' ' + ' '.join(f'[{key}]' for key in dict.fromkeys(
                    c['source_id'] for c in claim['citations'])) for claim in row['claims']]
            response = AnswerResponse(status=row['status'], answer='\n\n'.join(lines + row['limitations']),
                claims=row['claims'], sources=sources, limitations=row['limitations'], fallback_reason=row['reason'],
                relevance=row['relevance'], timings=AnswerTimings(retrieval_seconds=row['retrieval_seconds'],
                    generation_seconds=0.0, verification_seconds=0.0, total_seconds=time.perf_counter() - started))
            outcome = response.status
            return response
        except TimeoutError:
            raise ApiError('answer_timeout', 504) from None
        except RetrievalError as exc:
            invalid = exc.code in {'invalid_question', 'question_too_long', 'question_exceeds_model_limit', 'invalid_top_k'}
            raise ApiError(exc.code, 422 if invalid else 503) from None
        except Exception:
            raise ApiError('answer_unavailable') from None
        finally:
            self.owner.busy = False
            self.owner._idle.set()
            logger.info('answer_finished request_id=%s status=%s seconds=%.3f',
                        request_id, outcome, time.perf_counter() - started)
