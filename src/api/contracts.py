"""HTTP retrieval contracts. Source metadata comes only from storage."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from src.answering.contracts import AnswerStatus, Claim


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class AnswerHealthResponse(StrictModel):
    status: Literal['ready', 'unavailable']
    reason: Literal['answer_disabled', 'answer_semantics_required',
                    'answer_catalog_unavailable', 'dependency_unavailable'] | None


class RetrieveRequest(StrictModel):
    text: str = Field(min_length=1, max_length=8000)
    hrm_user_id: int | None = Field(default=None, gt=0, le=2**53 - 1,
        description='HRM user_id (backend odooId), not employee_id or internal UUID. '
                    'Backend must bind this reference to its authenticated caller. Requires X-Rita-Service-Key.')
    top_k: int = Field(default=5, ge=1, le=20,
                       description='Maximum number of excerpts, not a target count. Relevance filtering is server-configured. '
                       'Omit to use RETRIEVAL_TOP_K (default 5).')

    @field_validator('text', mode='before')
    @classmethod
    def nonempty(cls, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Question must be a nonempty string.')
        return value.strip()


class Match(StrictModel):
    chunk_id: str
    chunk_index: int
    run_id: str
    document_id: str
    revision_id: str
    content: str
    cosine_distance: float = Field(allow_inf_nan=False)
    rerank_score: float | None = Field(default=None, allow_inf_nan=False,
        description='Raw reranker logit, higher ranks first. Not a probability or completeness score.')
    title: str | None
    author_id: str | None
    role: str | None
    source_path: str
    version_label: str | None
    effective_from: str | None
    effective_to: str | None
    applicability: str | None
    source_spans: list[dict]


class Timings(StrictModel):
    retrieval_seconds: float
    total_seconds: float
    rerank_seconds: float = 0.0


class Relevance(StrictModel):
    status: Literal['unassessed', 'low_relevance', 'relevant_candidates', 'no_candidates'] = 'unassessed'
    warnings: list[str] = Field(default_factory=lambda: ['evidence_completeness_not_assessed'])


FallbackReason = Literal['below_relevance_threshold', 'relevance_unavailable',
                         'no_candidates', 'no_eligible_documents']


class RetrievalResponse(StrictModel):
    status: Literal['matches_found', 'no_eligible_documents', 'no_matches']
    matches: list[Match]
    fallback_reason: FallbackReason | None
    timings: Timings
    relevance: Relevance = Field(default_factory=Relevance)

    @model_validator(mode='after')
    def consistent_outcome(self):
        valid = ((self.status == 'matches_found' and bool(self.matches) and self.fallback_reason is None)
                 or (self.status == 'no_eligible_documents' and not self.matches
                     and self.fallback_reason == 'no_eligible_documents')
                 or (self.status == 'no_matches' and not self.matches
                     and self.fallback_reason in {'below_relevance_threshold', 'relevance_unavailable', 'no_candidates'}))
        if not valid:
            raise ValueError('inconsistent_retrieval_outcome')
        return self


class ApiError(Exception):
    def __init__(self, code, status_code=503):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class AnswerTimings(StrictModel):
    retrieval_seconds: float
    generation_seconds: float
    verification_seconds: float
    total_seconds: float


class AnswerSource(StrictModel):
    source_id: str
    title: str | None
    applicability: str | None
    effective_from: str | None
    effective_to: str | None
    version_label: str | None
    origins: list[dict]


class AnswerResponse(StrictModel):
    status: AnswerStatus
    answer: str
    claims: list[Claim]
    sources: list[AnswerSource]
    limitations: list[str]
    fallback_reason: str | None
    relevance: Relevance
    timings: AnswerTimings


EmployeeField = Literal['employee_name', 'employee_code', 'job_title', 'department_name',
                        'manager_name', 'probation_date', 'probation_end_date', 'official_date', 'resignation_date']


class EmployeeFact(StrictModel):
    ref_id: str
    field: EmployeeField
    label: str
    value: str | None


class EmployeeOrigin(StrictModel):
    relation: Literal['employee_sso_store'] = 'employee_sso_store'
    observed_at: str
    updated_at: str | None


class EmployeeEvidence(StrictModel):
    status: Literal['found', 'not_found', 'needs_clarification', 'unsupported']
    facts: list[EmployeeFact]
    source: EmployeeOrigin | None
    reason: str | None

    @model_validator(mode='after')
    def consistent_employee_evidence(self):
        if self.status == 'found':
            if not self.facts or self.source is None or len({f.field for f in self.facts}) != len(self.facts):
                raise ValueError('invalid_employee_evidence')
        elif self.facts:
            raise ValueError('invalid_employee_evidence')
        if len({f.ref_id for f in self.facts}) != len(self.facts):
            raise ValueError('invalid_employee_references')
        return self


class ContextRetrievalResponse(StrictModel):
    source_type: Literal['type_b', 'mixed']
    status: Literal['evidence_found', 'partial_evidence', 'no_evidence', 'needs_clarification', 'unsupported']
    matches: list[Match]
    employee_evidence: EmployeeEvidence
    document_fallback_reason: FallbackReason | None
    relevance: Relevance
    timings: Timings


class EmployeeClaim(StrictModel):
    text: str
    fact_refs: list[str] = Field(min_length=1)


class ContextAnswerResponse(AnswerResponse):
    source_type: Literal['type_b', 'mixed']
    employee_evidence: EmployeeEvidence
    employee_claims: list[EmployeeClaim]

    @model_validator(mode='after')
    def valid_employee_claims(self):
        references = {fact.ref_id for fact in self.employee_evidence.facts}
        if any(not set(claim.fact_refs) <= references for claim in self.employee_claims):
            raise ValueError('invalid_employee_claim_references')
        return self
