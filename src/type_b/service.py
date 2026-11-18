"""HTTP-owned A/B coordination. All personnel facts come from the readonly source."""
import asyncio
from datetime import datetime, timezone
import logging
import time
from uuid import uuid4

from src.api.contracts import (
    ApiError, AnswerTimings, ContextAnswerResponse, ContextRetrievalResponse,
    EmployeeClaim, EmployeeEvidence, EmployeeFact, EmployeeOrigin, Relevance, Timings,
)
from src.retrieval import RetrievalError
from .tasks import BY_FIELD, route_question

logger = logging.getLogger(__name__)
DEADLINE_SECONDS = 120
UNASSESSED = Relevance(status='no_candidates', warnings=['document_retrieval_not_requested'])
CLARIFY = 'Bạn vui lòng nêu rõ thông tin công việc của bản thân cần tra cứu: phòng ban, chức danh, quản lý hoặc mốc ngày công tác.'
UNSUPPORTED = 'Tác vụ này chưa được hỗ trợ. Hiện chỉ tra cứu một số thông tin công việc của bản thân; chưa tra cứu người khác hoặc dữ liệu nhạy cảm.'
SEPARATE = 'Thông tin chính sách được trình bày riêng để tham khảo; chưa có kết luận về quyền lợi hoặc điều kiện áp dụng cho cá nhân bạn.'


class TypeBCoordinator:
    def __init__(self, owner, employee_service=None):
        self.owner = owner
        self.settings = owner.settings.type_b
        self.employee = None
        self.source_error_type = ()
        if self.settings.enabled:
            from src.employee_source import EmployeeSourceService, EmployeeSourceError
            self.source_error_type = EmployeeSourceError
            self.employee = employee_service if employee_service is not None else EmployeeSourceService()
        self.available = False

    async def start(self):
        if not self.settings.enabled:
            return
        try:
            async with asyncio.timeout(15):
                await self.employee.check_source()
            self.available = True
        except Exception:
            # Personnel/driver diagnostics must never enter HTTP startup logs.
            logger.warning('type_b_startup state=unavailable')

    async def close(self):
        self.available = False
        if self.employee is not None:
            await self.employee.close()

    async def execute(self, text, top_k, hrm_user_id=None, *, answer=False):
        if not self.settings.enabled:
            if hrm_user_id is not None:
                raise ApiError('type_b_disabled')
            return await (self.owner.answers.answer(text, top_k) if answer else self.owner.retrieve(text, top_k))
        if not self.owner.started:
            raise ApiError('not_ready')
        if answer and not self.owner.settings.answer.enabled:
            raise ApiError('answer_unavailable')
        if self.owner.busy:
            raise ApiError('busy', 429)
        self.owner.busy = True
        self.owner._idle.clear()
        started, request_id, outcome = time.perf_counter(), uuid4().hex, 'failed'
        try:
            async with asyncio.timeout(DEADLINE_SECONDS):
                from src.retrieval.service import validate_request
                validate_request(text, top_k)
                route = route_question(text)
                prepared = None if answer else await self.owner.retrieval.prepare_query(text, top_k)
                if route.source == 'type_a':
                    documents = (await self.owner.answers.document_evidence(text, top_k) if answer
                                 else await self._documents(prepared, top_k))
                    elapsed = time.perf_counter() - started
                    response = (self.owner.answers.render_documents(text, documents, started, elapsed) if answer
                                else self.owner.retrieval_response(documents, started, elapsed))
                else:
                    if hrm_user_id is None:
                        raise ApiError('hrm_user_id_required', 422)
                    evidence = await self._employee_evidence(route, hrm_user_id)
                    documents = None
                    if route.source == 'mixed' and route.reason is None and (not answer or self.owner.answers.available):
                        documents = (await self.owner.answers.document_evidence(text, top_k) if answer
                                 else await self._documents(prepared, top_k))
                    elapsed = time.perf_counter() - started
                    response = (self._answer(route, evidence, documents, text, started, elapsed) if answer
                                else self._retrieval(route, evidence, documents, started, elapsed))
                outcome = response.status
                return response
        except ApiError:
            raise
        except self.source_error_type as error:
            raise ApiError('employee_source_timeout' if error.code == 'source_timeout' else
                           'employee_source_unavailable', 504 if error.code == 'source_timeout' else 503) from None
        except RetrievalError as error:
            invalid = error.code in {'invalid_question', 'question_too_long', 'question_exceeds_model_limit', 'invalid_top_k'}
            raise ApiError(error.code, 422 if invalid else 503) from None
        except TimeoutError:
            raise ApiError('answer_timeout' if answer else 'retrieval_timeout', 504) from None
        except Exception:
            raise ApiError('context_processing_failed') from None
        finally:
            self.owner.busy = False
            self.owner._idle.set()
            logger.info('context_finished request_id=%s status=%s seconds=%.3f',
                        request_id, outcome, time.perf_counter() - started)

    async def _documents(self, prepared, top_k):
        result = await self.owner.retrieval.retrieve_prepared(prepared, top_k)
        if result['status'] == 'model_mismatch':
            raise ApiError('model_mismatch')
        return result

    async def _employee_evidence(self, route, user_id):
        if route.reason is not None:
            unsupported = route.reason in ('other_employee_unsupported', 'employee_task_unsupported')
            return EmployeeEvidence(status='unsupported' if unsupported else 'needs_clarification',
                                    facts=[], source=None, reason=route.reason)
        if not self.available:
            raise ApiError('employee_source_unavailable')
        profile = await self.employee.find_by_user_id(user_id)
        source = EmployeeOrigin(observed_at=datetime.now(timezone.utc).isoformat(),
                                updated_at=profile.updated_at if profile else None)
        if profile is None:
            return EmployeeEvidence(status='not_found', facts=[], source=source, reason='employee_not_found')
        facts = [EmployeeFact(ref_id=f'B{index}', field=field, label=BY_FIELD[field].label,
                              value=getattr(profile, field)) for index, field in enumerate(route.fields, 1)]
        return EmployeeEvidence(status='found', facts=facts, source=source,
            reason='employee_fields_unavailable' if any(f.value is None for f in facts) else None)

    def _retrieval(self, route, evidence, documents, started, elapsed):
        known = sum(f.value is not None for f in evidence.facts)
        if evidence.status in ('needs_clarification', 'unsupported'):
            status = evidence.status
        elif route.source == 'mixed':
            status = ('evidence_found' if known == len(evidence.facts) and known and documents['matches'] else
                      'partial_evidence' if known or documents['matches'] else 'no_evidence')
        else:
            status = ('evidence_found' if known and known == len(evidence.facts) else
                      'partial_evidence' if known else 'no_evidence')
        return ContextRetrievalResponse(source_type=route.source, status=status,
            matches=documents['matches'] if documents else [], employee_evidence=evidence,
            document_fallback_reason=documents.get('fallback_reason') if documents else None,
            relevance=documents['relevance'] if documents else UNASSESSED.model_copy(deep=True),
            timings=Timings(retrieval_seconds=elapsed, rerank_seconds=documents.get('rerank_seconds', 0.0) if documents else 0.0,
                            total_seconds=time.perf_counter() - started))

    def _answer(self, route, evidence, documents, text, started, elapsed):
        employee_claims, lines = [], []
        for fact in evidence.facts:
            if fact.value is None:
                value = f'Hồ sơ hiện chưa có thông tin về {fact.label.lower()}.'
            else:
                display = (datetime.strptime(fact.value, '%Y-%m-%d').strftime('%d/%m/%Y')
                           if BY_FIELD[fact.field].date else fact.value)
                value = f'{fact.label} của bạn: {display}.'
            employee_claims.append(EmployeeClaim(text=value, fact_refs=[fact.ref_id]))
            lines.append(f'{value} [{fact.ref_id}]')
        known = sum(f.value is not None for f in evidence.facts)
        status = 'answered' if known and known == len(evidence.facts) else 'partial' if known else 'insufficient_evidence'
        limitations, claims, sources = [], [], []
        reason = evidence.reason
        if evidence.status == 'needs_clarification':
            status, limitations = 'needs_clarification', [CLARIFY]
        elif evidence.status == 'unsupported':
            status, limitations = 'unsupported', [UNSUPPORTED]
        elif evidence.status == 'not_found':
            limitations = ['Chưa tìm thấy hồ sơ nhân viên tương ứng trong nguồn HRM.']
        if route.source == 'mixed' and route.reason is None:
            personnel = '\n'.join(lines)
            lines = ['Thông tin hồ sơ:\n' + personnel] if personnel else []
            if self.owner.answers.available:
                document_answer = self.owner.answers.render_documents(text, documents, started, elapsed)
                claims, sources = document_answer.claims, document_answer.sources
                lines.append('Thông tin chính sách:\n' + document_answer.answer)
            else:
                limitations.append('Phần trả lời chính sách chưa sẵn sàng; bạn có thể dùng endpoint retrieve để xem bằng chứng tài liệu.')
            limitations.append(SEPARATE)
            status = 'partial' if known or claims else 'insufficient_evidence'
            reason = 'mixed_individual_eligibility_not_evaluated'
        return ContextAnswerResponse(source_type=route.source, status=status,
            answer='\n\n'.join(lines + limitations), claims=claims, sources=sources,
            employee_evidence=evidence, employee_claims=employee_claims, limitations=limitations,
            fallback_reason=reason, relevance=documents['relevance'] if documents else UNASSESSED.model_copy(deep=True),
            timings=AnswerTimings(retrieval_seconds=elapsed, generation_seconds=0.0,
                                 verification_seconds=0.0, total_seconds=time.perf_counter() - started))
