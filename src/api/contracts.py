"""HTTP retrieval contracts. Source metadata comes only from storage."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class RetrieveRequest(StrictModel):
    text: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=5, ge=1, le=20,
                       description='Maximum number of accepted excerpts, not a target count. Omit to use RETRIEVAL_TOP_K (default 5).')

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
