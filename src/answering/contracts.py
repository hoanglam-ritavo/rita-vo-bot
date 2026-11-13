"""Validated internal model output and public response contracts."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class AskRequest(StrictModel):
    text: str = Field(min_length=1, max_length=8000)

    @field_validator('text', mode='before')
    @classmethod
    def nonempty(cls, value):
        if not isinstance(value, str):
            raise ValueError('Question must be a string.')
        value = value.strip()
        if not value:
            raise ValueError('Question must not be empty.')
        return value


Status = Literal['answered', 'insufficient_evidence', 'needs_clarification', 'unsupported']


class ModelAnswer(StrictModel):
    answer: str = Field(min_length=1, max_length=12000)
    status: Status
    citations: list[str]
    support: Literal['direct', 'partial', 'indirect', 'none']
    conflict: bool
    reason: str = Field(min_length=1, max_length=2000)


class Confidence(StrictModel):
    level: Literal['high', 'medium', 'low', 'insufficient']
    reason: str
    calibrated: Literal[False] = False


class Source(StrictModel):
    id: str
    chunk_id: str
    document_id: str
    revision_id: str
    title: str
    source_spans: list[dict]


class Timings(StrictModel):
    retrieval_seconds: float
    generation_seconds: float
    total_seconds: float


class Answer(StrictModel):
    answer: str
    status: Status
    confidence: Confidence
    sources: list[Source]
    timings: Timings


class AnswerError(Exception):
    def __init__(self, code, status_code=503):
        self.code = code
        self.status_code = status_code
        super().__init__(code)
