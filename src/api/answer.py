"""Template answer lifecycle sharing retrieval ownership; no GGUF dependencies."""
import asyncio
import logging
import time
from uuid import uuid4
from src.answering.catalog import load_catalog
from src.answering.pipeline import evaluate_question, render, evidence_result
from .admission import admit, release
from src.retrieval import RetrievalError
from .contracts import AnswerResponse, AnswerTimings, ApiError

logger = logging.getLogger(__name__)
DEADLINE_SECONDS = 120


def format_answer(row, started):
    referenced = {c['source_id'] for claim in row['claims'] for c in claim['citations']}
    sources = [dict(source_id=key, **{k: v for k, v in source.items() if k != 'content'})
               for key, source in row['sources'].items() if key in referenced]
    lines = ([row['intro']] if row['intro'] else []) + [
        claim['text'] + ' ' + ' '.join(f'[{key}]' for key in dict.fromkeys(
            c['source_id'] for c in claim['citations'])) for claim in row['claims']]
    return AnswerResponse(status=row['status'], answer='\n\n'.join(lines + row['limitations']),
        claims=row['claims'], sources=sources, limitations=row['limitations'], fallback_reason=row['reason'],
        relevance=row['relevance'], timings=AnswerTimings(retrieval_seconds=row.get('retrieval_seconds', 0.0),
            generation_seconds=0.0, verification_seconds=0.0, total_seconds=time.perf_counter() - started))


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
        self.state = 'unavailable'
        self.reason = 'dependency_unavailable'

    def _set_state(self, state, reason):
        self.state, self.reason = state, reason
        self.available = state == 'ready'
        if not self.available:
            self.catalog = self.lexicon = None

    async def start(self):
        if not self.settings.answer.enabled:
            self._set_state('disabled', 'answer_disabled')
            logger.info('answer_startup state=%s reason=%s', self.state, self.reason)
            return
        if self.settings.semantic.mode != 'expand':
            self._set_state('unavailable', 'answer_semantics_required')
            logger.warning('answer_startup state=%s reason=%s', self.state, self.reason)
            return
        try:
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
            self._set_state('ready', None)
            logger.info('answer_startup state=%s reason=%s', self.state, self.reason)
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception:
            self._set_state('unavailable', 'answer_catalog_unavailable')
            logger.warning('answer_startup state=%s reason=%s', self.state, self.reason)

    async def close(self):
        self._set_state('stopped', 'dependency_unavailable')

    async def document_evidence(self, text, top_k, *, contextual=False, analysis=None):
        if not self.available:
            raise ApiError('answer_unavailable')
        return await evidence_result(text, self.owner.retrieval, self.catalog, self.lexicon, top_k, contextual=contextual, analysis=analysis)

    def render_documents(self, text, result, started, elapsed, *, contextual=False, analysis=None):
        if not self.available:
            raise ApiError('answer_unavailable')
        row = render(text, result, self.catalog, self.lexicon, getattr(self.owner.retrieval, 'policy', None), contextual=contextual, analysis=analysis)
        return format_answer({**row, 'retrieval_seconds': elapsed}, started)

    async def answer(self, text, top_k):
        if not self.owner.started or not self.available:
            raise ApiError('answer_unavailable')
        admit(self.owner)
        started, request_id, outcome = time.perf_counter(), uuid4().hex, 'failed'
        try:
            async with asyncio.timeout(DEADLINE_SECONDS):
                row = await evaluate_question(text, self.owner.retrieval, self.catalog, self.lexicon,
                    top_k=top_k)
            response = format_answer(row, started)
            outcome = response.status
            return response
        except TimeoutError:
            raise ApiError('answer_timeout', 504) from None
        except RetrievalError as exc:
            invalid = exc.code in {'invalid_question', 'question_too_long', 'question_exceeds_model_limit', 'invalid_top_k'}
            raise ApiError(exc.code, 422 if invalid else 503) from None
        except Exception as exc:
            logger.warning('answer_processing_failed request_id=%s exception_type=%s',
                           request_id, type(exc).__name__)
            raise ApiError('answer_unavailable') from None
        finally:
            release(self.owner)
            logger.info('answer_finished request_id=%s status=%s seconds=%.3f',
                        request_id, outcome, time.perf_counter() - started)
