"""Request-owned unified document/personnel coordination; no source-selection branch."""
from __future__ import annotations

import asyncio
import logging
import time
from uuid import uuid4
from typing import TYPE_CHECKING

from src.api.contracts import (ApiError, AnswerTimings, Timings, Relevance,
                               UnifiedAnswerResponse, UnifiedRetrievalResponse)
from src.answering.contextual import prepare, requirements, compose, document_result
from src.answering.discovery import suggest_document_topics
from src.answering.scope import resolve_document_scopes
from src.api.admission import admit, release
from src.retrieval import RetrievalError

if TYPE_CHECKING:
    from .employee_store import EmployeeStoreReader

logger = logging.getLogger(__name__)
DEADLINE_SECONDS = 120
EMPLOYEE_ERROR_CODES = frozenset({
    'schema_incompatible', 'readonly_required', 'source_access_denied',
    'source_closed', 'source_timeout', 'source_unavailable', 'invalid_batch', 'invalid_data',
    'invalid_identity', 'invalid_list', 'invalid_projection', 'invalid_timestamp',
    'unexpected_identity', 'conflicting_rows',
})


class ContextCoordinator:
    def __init__(self, owner, employee_service: EmployeeStoreReader | None = None):
        self.owner = owner
        self.settings = owner.settings.type_b
        self.employee = None
        self.source_error_type = ()
        if self.settings.enabled:
            from .employee_store import EmployeeSourceService, EmployeeSourceError
            self.employee = employee_service if employee_service is not None else EmployeeSourceService()
            self.source_error_type = EmployeeSourceError
        self.available = False
        self._last_source_error = None
        self._next_source_check = 0.0
        self._source_check_lock = asyncio.Lock()

    async def start(self):
        await self.check_source()

    def _source_error_code(self, error):
        from src.database.config import ConfigurationError

        if isinstance(error, ConfigurationError):
            return 'invalid_source_configuration'
        if isinstance(error, TimeoutError):
            return 'source_timeout'
        if isinstance(error, self.source_error_type) and error.code in EMPLOYEE_ERROR_CODES:
            return error.code
        return 'source_unavailable'

    async def check_source(self):
        """Return whether a needed check was deferred by the cooldown or another check."""
        if not self.settings.enabled or self.employee is None or self.available:
            return False
        if time.monotonic() < self._next_source_check or self._source_check_lock.locked():
            return True
        async with self._source_check_lock:
            self._next_source_check = time.monotonic() + 30
            try:
                async with asyncio.timeout(15):
                    await self.employee.check_source()
                self.available = True
                self._last_source_error = None
                logger.info('employee_context state=ready')
            except Exception as error:
                self._last_source_error = self._source_error_code(error)
                logger.warning('employee_context state=unavailable stage=check_source reason=%s',
                               self._last_source_error)
            finally:
                self._next_source_check = time.monotonic() + 30
        return False

    async def close(self):
        self.available = False
        self._last_source_error = None
        if self.employee is not None:
            await self.employee.close()

    async def _lookup_employee(self, hrm_user_id, fields, manager, *, message_id, attempt):
        started = time.perf_counter()
        snapshot, reason = None, None
        state = 'disabled' if not self.settings.enabled else 'unavailable'
        stage = 'check_source'
        check_deferred = await self.check_source()
        if self.settings.enabled and self.available:
            stage = 'read_context'
            try:
                snapshot = await self.employee.read_context(hrm_user_id, fields, manager=manager)
                state = 'applied' if snapshot is not None else 'not_found'
            except self.source_error_type as error:
                from .employee_store.errors import EmployeeValidationError
                record_error = isinstance(error, EmployeeValidationError)
                reason = self._source_error_code(error)
                if not record_error:
                    self.available = False
                    self._last_source_error = reason
                    self._next_source_check = time.monotonic() + 30
                logger.warning('employee_context state=unavailable stage=read_context scope=%s reason=%s '
                               'message_id=%s attempt=%s',
                               'record' if record_error else 'source', reason, message_id, attempt)
        elif self.settings.enabled:
            reason = self._last_source_error

        if message_id is not None:
            missing_fields = (tuple(field for field in fields if snapshot.values.get(field) is None)
                              if snapshot is not None else ())
            logger.info('employee_context_lookup message_id=%s attempt=%s stage=%s state=%s reason=%s '
                        'check_deferred=%s requested_fields=%s missing_fields=%s manager_requested=%s elapsed_ms=%.1f',
                        message_id, attempt, stage, state, reason or '-', check_deferred,
                        ','.join(fields) or '-', ','.join(missing_fields) or '-', manager,
                        (time.perf_counter() - started) * 1000)
        return snapshot, state

    async def execute(self, text, top_k, hrm_user_id=None, *, answer=False, analysis=None, reserved=False,
                      on_resolved=None, message_id=None, attempt=None):
        if type(hrm_user_id) is not int or not 0 < hrm_user_id <= 2**53-1:
            raise ApiError('hrm_user_id_required', 422)
        if not self.owner.started:
            raise ApiError('not_ready')
        if answer and not self.owner.settings.answer.enabled:
            raise ApiError('answer_unavailable')
        if not reserved:
            admit(self.owner)
        started, request_id = time.perf_counter(), uuid4().hex
        try:
            async with asyncio.timeout(DEADLINE_SECONDS):
                from src.retrieval.service import validate_request
                validate_request(text, top_k)
                answers = self.owner.answers
                policy = getattr(self.owner.retrieval, 'policy', None)
                plan = prepare(text, answers.catalog, answers.lexicon, policy, analysis=analysis)
                scoped_evidence = None
                discovery_seconds = 0.
                if answer and answers.available:
                    resolved, scoped_evidence = await resolve_document_scopes(plan.analysis, answers.catalog,
                                                                              self.owner.retrieval)
                    if resolved.topic_unmatched and on_resolved is not None:
                        resolved, discovery_seconds = await suggest_document_topics(resolved, answers.catalog,
                                                                                   self.owner.retrieval)
                    if resolved is not plan.analysis:
                        plan = prepare(text, answers.catalog, answers.lexicon, policy, analysis=resolved)
                if on_resolved is not None:
                    on_resolved(plan.analysis)
                text = plan.question
                if answer and not answers.available and not plan.direct_fields:
                    raise ApiError('answer_unavailable')
                if plan.unsupported_person or plan.clarification:
                    status = 'unsupported' if plan.unsupported_person else 'needs_clarification'
                    message = ('Chỉ sử dụng hồ sơ của người đang đăng nhập; không tra nhân viên khác từ tên hoặc email.'
                               if plan.unsupported_person else plan.clarification)
                    logger.info('request_plan state=%s reason=%s', status,
                                'other_subject' if plan.unsupported_person else
                                'topic_suggestions' if plan.analysis.discovery_options else
                                'unresolved_request' if plan.analysis.unresolved else
                                'multiple_scopes' if len(plan.analysis.cards) > 1 else
                                'topic_unmatched' if not (plan.analysis.parts or plan.analysis.missing_slots
                                                          or plan.analysis.ambiguous) else 'needs_detail')
                    elapsed = time.perf_counter() - started
                    warning = 'document_retrieval_not_requested'
                    if discovery_seconds:
                        warning = 'topic_discovery_suggestions_only' if plan.analysis.discovery_options else 'topic_discovery_no_verified_options'
                    relevance = Relevance(status='no_candidates', warnings=[warning])
                    if not answer:
                        return UnifiedRetrievalResponse(status=status, document_status=None, matches=[],
                            fallback_reason=None, relevance=relevance, timings=Timings(
                                retrieval_seconds=0., total_seconds=elapsed))
                    return UnifiedAnswerResponse(status=status, answer=message, claims=[], sources=[],
                        limitations=[message], fallback_reason='other_employee_unsupported' if plan.unsupported_person else plan.analysis.clarification_reason,
                        relevance=relevance, timings=AnswerTimings(retrieval_seconds=discovery_seconds, generation_seconds=0.,
                            verification_seconds=0., total_seconds=elapsed))
                fields, manager = requirements(plan)
                snapshot, context_state = None, 'not_needed'
                if fields or manager:
                    snapshot, context_state = await self._lookup_employee(
                        hrm_user_id, fields, manager, message_id=message_id, attempt=attempt)
                document, raw = None, None
                direct_only = bool(plan.direct_fields) and plan.card is None and not plan.analysis.document_lookup
                if not direct_only:
                    if answer and answers.available:
                        raw = scoped_evidence
                        if raw is None:
                            raw = await answers.document_evidence(text, top_k, contextual=True, analysis=plan.analysis)
                        document = answers.render_documents(text, raw, started,
                            time.perf_counter()-started, contextual=True, analysis=plan.analysis)
                    elif not answer:
                        prepared = await self.owner.retrieval.prepare_query(plan.analysis.document_question or text, top_k)
                        raw = await self.owner.retrieval.retrieve_prepared(prepared, top_k)
                        if raw['status'] == 'model_mismatch':
                            raise ApiError('model_mismatch')
                evidence, claims = document_result(document) if document is not None else ([], [])
                document_claim_count = len(claims)
                application, context_limits = compose(plan, snapshot, context_state, evidence, claims,
                                                       calculate_values=answer)
                elapsed = time.perf_counter()-started
                relevance = document.relevance if document else Relevance.model_validate(
                    raw.get('relevance', {'status':'unassessed','warnings':['evidence_completeness_not_assessed']})) if raw else Relevance(status='no_candidates', warnings=['document_retrieval_not_requested'])
                if not answer:
                    documents = self.owner.retrieval_response(raw, started, elapsed) if raw else None
                    known = bool(documents and documents.matches) or any(e['kind'] == 'employee' and e['value'] is not None for e in evidence)
                    status = 'partial_evidence' if known and application.state in ('partial', 'unavailable', 'not_found', 'disabled', 'conflict') else 'evidence_found' if known else 'no_evidence'
                    return UnifiedRetrievalResponse(status=status, document_status=documents.status if documents else None,
                        matches=documents.matches if documents else [], fallback_reason=documents.fallback_reason if documents else None,
                        evidence=evidence, application=application, relevance=relevance,
                        timings=Timings(retrieval_seconds=elapsed, rerank_seconds=documents.timings.rerank_seconds if documents else 0., total_seconds=elapsed))
                limitations = list(document.limitations) if document else []
                limitations.extend(context_limits)
                limitations.extend(plan.analysis.scope_limits)
                if plan.clarification:
                    limitations.append(plan.clarification)
                if plan.unsupported_person:
                    limitations.append('Chỉ sử dụng hồ sơ của người đang đăng nhập; không tra nhân viên khác từ tên hoặc email.')
                if len(claims) > 12:
                    claims = claims[:8] + claims[document_claim_count:document_claim_count+4] if document_claim_count >= 8 else claims[:12]
                    limitations.append('Câu trả lời vượt phạm vi hiển thị của một lượt; cần tách yêu cầu để xem đầy đủ.')
                # Do not serialize orphaned personnel values when output was bounded.
                used = {ref for claim in claims for ref in claim['evidence_refs']}
                nodes = {item['id']:item for item in evidence}
                def include(key):
                    item = nodes[key]
                    for ref in item.get('input_refs', []) + item.get('rule_refs', []):
                        if ref not in used:
                            used.add(ref)
                            include(ref)
                for key in tuple(used):
                    include(key)
                evidence = [item for item in evidence if item['id'] in used]
                complete = (document is None or document.status == 'answered') and application.state in ('not_needed', 'applied')
                status = 'answered' if claims and complete and not limitations else 'partial' if claims else (
                    document.status if document else 'insufficient_evidence')
                if plan.unsupported_person and not claims:
                    status = 'unsupported'
                elif plan.clarification and not claims:
                    status = 'needs_clarification'
                if claims and not any(item['kind']=='document' or item['kind']=='calculation' or
                        item['kind']=='employee' and item['value'] is not None for item in evidence):
                    status = 'insufficient_evidence'
                if not claims and not limitations:
                    limitations.append('Chưa xác định được bằng chứng hoặc tác vụ phù hợp; bạn hãy nêu rõ yêu cầu.')
                lines = [f'{claim["text"]} [{claim["id"]}]' for claim in claims]
                if claims and plan.analysis.scope_intro:
                    lines.insert(0, plan.analysis.scope_intro)
                return UnifiedAnswerResponse(status=status,
                    answer='\n\n'.join(lines if claims else list(dict.fromkeys(limitations))),
                    claims=claims, sources=[source for source in document.sources if source.source_id in {item['source_id'] for item in evidence if item['kind']=='document'}] if document else [], evidence=evidence,
                    application=application, limitations=list(dict.fromkeys(limitations)),
                    fallback_reason=document.fallback_reason if document else None, relevance=relevance,
                    timings=AnswerTimings(retrieval_seconds=elapsed, generation_seconds=0., verification_seconds=0., total_seconds=elapsed))
        except RetrievalError as error:
            invalid = error.code in {'invalid_question', 'question_too_long', 'question_exceeds_model_limit', 'invalid_top_k'}
            raise ApiError(error.code, 422 if invalid else 503) from None
        except TimeoutError:
            raise ApiError('answer_timeout' if answer else 'retrieval_timeout', 504) from None
        except ApiError:
            raise
        except Exception as error:
            logger.warning('context_processing_failed request_id=%s exception_type=%s', request_id, type(error).__name__)
            raise ApiError('answer_unavailable' if answer else 'retrieval_failed') from None
        finally:
            if not reserved:
                release(self.owner)
            logger.info('context_finished request_id=%s seconds=%.3f', request_id, time.perf_counter()-started)
