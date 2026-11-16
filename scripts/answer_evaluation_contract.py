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


from src.answering.contracts import Citation, Claim, Draft, Check, Verdict, validate_draft, accepted


from src.answering.acceptance import evaluate_gates
