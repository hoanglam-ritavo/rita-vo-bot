"""Shared case validation for offline evaluation and preparation; no IO."""
from src.ingestion import document_packages as packages


def validate_case_data(data, record, payload):
    from .service import validate_request
    if (data.get('revision_id') != record['revision_id'] or
            data.get('chunks_sha256') != packages.canonical_hash(payload['chunks'])):
        raise ValueError('stale_cases')
    cases = data.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('empty_cases')
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError('invalid_case')
        if not isinstance(case.get('id'), str) or not case['id'] or case['id'] in seen:
            raise ValueError('invalid_case_id')
        seen.add(case['id'])
        validate_request(case.get('question'), 5)
        if case.get('category') not in {'detail', 'condition', 'aggregate', 'unsupported'}:
            raise ValueError('invalid_category')
        if type(case.get('critical', False)) is not bool:
            raise ValueError('invalid_critical')
        groups = case.get('required_groups')
        if not isinstance(groups, list) or (not groups and case['category'] != 'unsupported'):
            raise ValueError('invalid_evidence_groups')
        for group in groups:
            if (not isinstance(group, list) or not group or
                    any(type(i) is not int or not 0 <= i < len(payload['chunks']) for i in group)):
                raise ValueError('invalid_chunk_index')
    return cases
