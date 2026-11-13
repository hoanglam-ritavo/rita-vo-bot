"""HTTP retrieval contracts. Source metadata comes only from storage."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class RetrieveRequest(StrictModel):
    text: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=5, ge=1, le=20,
                       description='Omit to use RETRIEVAL_TOP_K from server configuration (default 5).')

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


class RetrievalResponse(StrictModel):
    status: Literal['matches_found', 'no_eligible_documents', 'no_matches']
    matches: list[Match]
    timings: Timings


class ApiError(Exception):
    def __init__(self, code, status_code=503):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
