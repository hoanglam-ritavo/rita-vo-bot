"""Shared answer schemas and source/verdict validation."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

AnswerStatus = Literal['answered', 'partial', 'needs_clarification', 'insufficient_evidence', 'unsupported']
STATUSES = set(AnswerStatus.__args__)

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Citation(Strict):
    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class Claim(Strict):
    text: str = Field(min_length=1, max_length=2000)
    citations: list[Citation] = Field(min_length=1, max_length=5)


class Draft(Strict):
    status: AnswerStatus
    claims: list[Claim] = Field(max_length=12)
    limitations: list[str] = Field(max_length=8)


class Check(Strict):
    claim_index: int = Field(ge=0)
    supported: bool
    conditions_preserved: bool


class Verdict(Strict):
    checks: list[Check] = Field(max_length=12)
    status_appropriate: bool
    missing_parts_disclosed: bool
    no_unresolved_conflict: bool


def validate_draft(raw, sources):
    draft = Draft.model_validate(raw)
    answering = draft.status in {'answered', 'partial'}
    if answering != bool(draft.claims):
        raise ValueError('inconsistent_answer')
    if any(not x.strip() or len(x) > 2000 for x in draft.limitations):
        raise ValueError('invalid_limitations')
    if draft.status != 'answered' and not any(x.strip() for x in draft.limitations):
        raise ValueError('missing_limitations')
    for claim in draft.claims:
        if not claim.text.strip():
            raise ValueError('empty_claim')
        for citation in claim.citations:
            source = sources.get(citation.source_id)
            if source is None or not citation.quote.strip() or citation.quote not in source['content']:
                raise ValueError('invalid_citation')
    return draft


def accepted(draft, raw_verdict):
    verdict = Verdict.model_validate(raw_verdict)
    indices = [check.claim_index for check in verdict.checks]
    return (sorted(indices) == list(range(len(draft.claims)))
            and all(c.supported and c.conditions_preserved for c in verdict.checks)
            and verdict.status_appropriate and verdict.missing_parts_disclosed
            and verdict.no_unresolved_conflict)


