"""Deterministic source-backed answer contracts; no model verdicts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

AnswerStatus = Literal['answered', 'partial', 'needs_clarification', 'insufficient_evidence', 'unsupported']


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Citation(Strict):
    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class Claim(Strict):
    text: str = Field(min_length=1, max_length=2000)
    citations: list[Citation] = Field(min_length=1, max_length=5)
