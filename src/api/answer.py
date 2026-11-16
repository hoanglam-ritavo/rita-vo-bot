"""Gated answer lifecycle; retrieval keeps working if answer prerequisites fail."""
import asyncio
import logging
import time

from src.answering.acceptance import load_acceptance
from src.answering.contracts import Draft
from src.answering.identity import DEADLINE_SECONDS
from src.answering.pipeline import evaluate_question
from src.answering.runtime import LocalRuntime
from src.retrieval import RetrievalError
from .contracts import AnswerResponse, AnswerTimings, ApiError

logger = logging.getLogger(__name__)
EMPTY = 'Chưa có đủ bằng chứng trong tài liệu để trả lời câu hỏi này. Vui lòng làm rõ yêu cầu hoặc kiểm tra tài liệu nguồn.'


async def drain(task):
    """Keep ownership through repeated ASGI cancellation, including process cleanup."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


class AnswerService:
    def __init__(self, settings, owner):
        self.settings, self.owner = settings, owner
        self.runtime = None
        self.corpus = None
        self.available = False

    async def start(self):
        config = self.settings.answer
        if not config.enabled:
            return
        try:
            if (not self.settings.relevance_filter_enabled or not self.settings.rerank.enabled
                    or config.report_path is None or config.review_path is None):
                raise ValueError('answer_prerequisites_missing')
            self.runtime = LocalRuntime(config.model_path, config.model_sha256)
            self.corpus = load_acceptance(config.report_path, config.review_path, self.settings,
                                          await asyncio.to_thread(self.runtime.verify_files))
            await self.runtime.start()
            await self.runtime.warmup()
            self.available = True
        except asyncio.CancelledError:
            await drain(asyncio.create_task(self.close()))
            raise
        except Exception:
            await self.close()
            logger.warning('answer_unavailable code=answer_prerequisites_or_runtime_failed')

    async def close(self):
        self.available = False
        if self.runtime is not None:
            await self.runtime.close()

    async def answer(self, text, top_k):
        if not self.owner.started or not self.available:
            raise ApiError('answer_unavailable')
        if self.owner.busy:
            raise ApiError('busy', 429)
        self.owner.busy = True
        self.owner._idle.clear()
        started = time.perf_counter()
        outcome = 'failed'
        try:
            async with asyncio.timeout(DEADLINE_SECONDS):
                row = await evaluate_question(text, self.owner.retrieval, self.runtime,
                                              top_k=top_k, expected_corpus=self.corpus)
            claims, sources, limitations = [], [], [EMPTY]
            if row['accepted']:
                draft = Draft.model_validate(row['draft'])
                claims, limitations = draft.claims, draft.limitations
                referenced = {citation.source_id for claim in claims for citation in claim.citations}
                sources = [dict(source_id=key, **{k: v for k, v in source.items() if k != 'content'})
                           for key, source in row['sources'].items() if key in referenced]
            lines = [claim.text + ' ' + ' '.join(f'[{key}]' for key in dict.fromkeys(
                c.source_id for c in claim.citations)) for claim in claims]
            response = AnswerResponse(status=row['status'], answer='\n\n'.join(lines + limitations),
                claims=claims, sources=sources, limitations=limitations, fallback_reason=row['reason'],
                timings=AnswerTimings(retrieval_seconds=row['retrieval_seconds'],
                    generation_seconds=row['generation_seconds'], verification_seconds=row['verification_seconds'],
                    total_seconds=time.perf_counter() - started))
            outcome = response.status
            return response
        except TimeoutError:
            await drain(asyncio.create_task(self.close()))
            raise ApiError('answer_timeout', 504) from None
        except asyncio.CancelledError:
            await drain(asyncio.create_task(self.close()))
            raise
        except RetrievalError as exc:
            invalid = exc.code in {'invalid_question', 'question_too_long', 'question_exceeds_model_limit', 'invalid_top_k'}
            raise ApiError(exc.code, 422 if invalid else 503) from None
        except Exception:
            # Unknown inference outcome invalidates the owned runtime; no automatic retry.
            await drain(asyncio.create_task(self.close()))
            raise ApiError('answer_unavailable') from None
        finally:
            self.owner.busy = False
            self.owner._idle.set()
            logger.info('answer_finished status=%s seconds=%.3f', outcome, time.perf_counter() - started)
