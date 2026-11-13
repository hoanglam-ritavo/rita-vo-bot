"""Offline answer evaluation contracts; never imported by the HTTP application."""
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.paths import REPOSITORY_ROOT


CATEGORIES = {'supported': 20, 'partial': 10, 'absent': 10, 'ambiguous': 10, 'adversarial': 10}
STATUSES = {'answered', 'partial', 'needs_clarification', 'insufficient_evidence', 'unsupported'}


def sha_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def private_path(path):
    path = Path(path).resolve()
    root = (REPOSITORY_ROOT / '.tmp').resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('evaluation_path_outside_tmp')
    return path


def write_new(path, value):
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def load_cases(path):
    """Require frozen, source-reviewed cases, never upgrade historical cases silently."""
    data = json.loads(private_path(path).read_text(encoding='utf-8'))
    cases = data.get('cases')
    if data.get('schema_version') != 1 or data.get('source_reviewed') is not True:
        raise ValueError('source_reviewed_v1_cases_required')
    if not isinstance(cases, list) or len(cases) != 60:
        raise ValueError('need_60_cases')
    identities, questions, groups = set(), set(), {}
    for case in cases:
        for key in ('id', 'intent_group', 'question', 'expected'):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError('invalid_case')
        if case['id'] in identities or case['question'].strip() in questions:
            raise ValueError('duplicate_case')
        identities.add(case['id'])
        questions.add(case['question'].strip())
        if len(case['question'].strip()) > 2000:
            raise ValueError('question_too_long')
        if (case.get('split') not in {'development', 'holdout'}
                or case.get('category') not in CATEGORIES
                or case.get('expected_status') not in STATUSES
                or type(case.get('critical')) is not bool):
            raise ValueError('invalid_case')
        family = case['intent_group']
        if family in groups and groups[family] != case['split']:
            raise ValueError('intent_group_leaks_across_splits')
        groups[family] = case['split']
        if case['category'] == 'supported' and case['expected_status'] != 'answered':
            raise ValueError('supported_case_must_expect_answer')
        if not isinstance(case.get('source_refs'), list):
            raise ValueError('source_refs_required')
        if case['category'] in {'supported', 'partial'} and not case['source_refs']:
            raise ValueError('source_refs_required')
        for ref in case['source_refs']:
            if (not isinstance(ref, dict) or not isinstance(ref.get('package'), str)
                    or not isinstance(ref.get('chunk_id'), str)):
                raise ValueError('invalid_source_ref')
    for split in ('development', 'holdout'):
        if Counter(c['category'] for c in cases if c['split'] == split) != {
                key: count // 2 for key, count in CATEGORIES.items()}:
            raise ValueError('need_balanced_splits')
    pins = data.get('packages')
    if not isinstance(pins, dict) or not pins:
        raise ValueError('package_pins_required')
    for relative, hashes in pins.items():
        package = (REPOSITORY_ROOT / relative).resolve()
        if not package.is_relative_to((REPOSITORY_ROOT / 'chunks').resolve()):
            raise ValueError('package_outside_chunks')
        if not isinstance(hashes, dict) or not {'metadata.json', 'chunks.json', 'extraction.json'} <= hashes.keys():
            raise ValueError('incomplete_package_pin')
        # Pin the source snapshot too, not only authored JSON.
        actual = {p.name: sha_file(p) for p in package.iterdir() if p.is_file()}
        if actual != hashes or len(actual) < 4:
            raise ValueError('stale_package_pin')
        chunks = json.loads((package / 'chunks.json').read_text(encoding='utf-8'))['chunks']
        ids = {chunk['id'] for chunk in chunks}
        for case in cases:
            for ref in case['source_refs']:
                if ref['package'] == relative and ref['chunk_id'] not in ids:
                    raise ValueError('unknown_source_chunk')
    if any(ref['package'] not in pins for c in cases for ref in c['source_refs']):
        raise ValueError('unpinned_source_ref')
    return data


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Citation(Strict):
    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)


class Claim(Strict):
    text: str = Field(min_length=1, max_length=2000)
    citations: list[Citation] = Field(min_length=1, max_length=5)


class Draft(Strict):
    status: str
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
    if draft.status not in STATUSES:
        raise ValueError('invalid_answer_status')
    answering = draft.status in {'answered', 'partial'}
    if answering != bool(draft.claims):
        raise ValueError('inconsistent_answer')
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


def evaluate_gates(report, reviews):
    """Manual grades are bound to the exact report hash by the CLI, never model scores."""
    rows = report.get('cases', [])
    expected_ids = {r['id'] for r in rows}
    if len(rows) != 60 or len(expected_ids) != 60:
        raise ValueError('complete_report_required')
    for split in ('development', 'holdout'):
        if Counter(r['category'] for r in rows if r['split'] == split) != {
                key: count // 2 for key, count in CATEGORIES.items()}:
            raise ValueError('balanced_report_required')
    graded = reviews.get('cases', [])
    if len(graded) != 60 or {r['id'] for r in graded} != expected_ids:
        raise ValueError('complete_manual_review_required')
    grades = {r['id']: r for r in graded}
    for grade in graded:
        for key in ('correct_and_complete', 'no_unsupported_claims', 'sources_valid', 'critical_passed',
                    'more_useful_than_excerpts'):
            if type(grade.get(key)) is not bool:
                raise ValueError('boolean_manual_grades_required')
    holdout = [r for r in rows if r['split'] == 'holdout']
    supported = [r for r in holdout if r['category'] == 'supported']
    if len(holdout) != 30 or len(supported) != 10:
        raise ValueError('balanced_report_required')
    published = [r for r in holdout if r.get('accepted')]
    critical = [r for r in rows if r['critical'] or r['category'] == 'adversarial']
    timings = [r['seconds'] for r in rows]
    if any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in timings):
        raise ValueError('invalid_timings')
    memory = report.get('peak_system_memory_percent')
    if type(memory) not in (int, float) or not math.isfinite(memory) or not 0 <= memory <= 100:
        raise ValueError('invalid_memory_measurement')
    gates = {
        'grounding': all(grades[r['id']]['no_unsupported_claims'] and grades[r['id']]['sources_valid'] for r in published),
        'supported_recall': sum(bool(r.get('accepted')) and r.get('status') == 'answered'
                                and grades[r['id']]['correct_and_complete'] for r in supported) / 10 >= .9,
        'critical': bool(critical) and all(grades[r['id']]['critical_passed'] for r in critical),
        'latency': sum(t <= 110 and r.get('error') is None for t, r in zip(timings, rows)) / 60 >= .95,
        'memory': memory <= 85,
        'value': sum(bool(r.get('accepted')) and grades[r['id']]['more_useful_than_excerpts'] for r in supported) / 10 >= .8,
        'real_runtime': report.get('runtime_measured') is True,
        'user_acceptance': reviews.get('user_accepted') is True,
    }
    return {'gates': gates, 'integration_ready': all(gates.values()),
            'notice': 'Manual source review and user acceptance are not model-verifier guarantees.'}
