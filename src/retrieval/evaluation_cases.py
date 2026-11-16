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


def validate_online_case(case):
    if case.get('split') not in ('calibration', 'holdout', 'regression') or type(case.get('relevant')) is not bool:
        raise ValueError('invalid_online_case')
    if case['relevant'] != bool(case['required_groups']):
        raise ValueError('invalid_online_case_evidence')


def validate_cases(entries, collect_only=False):
    cases = [c for c, _, _ in entries]
    if not cases or len({c['question'] for c in cases}) != len(cases) or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('duplicate_or_empty_cases')
    groups = {}
    for case in cases:
        family = case.get('intent_group')
        if not isinstance(family, str) or not family.strip():
            raise ValueError('intent_group_required')
        if family in groups and groups[family] != case['split']:
            raise ValueError('intent_group_leaks_across_splits')
        groups[family] = case['split']
    if collect_only:
        return
    if (len(cases) < 100 or sum(c['split'] == 'regression' for c in cases) < 20
            or any(sum(c['split'] == split and c['relevant'] is label for c in cases) < 20
                   for split in ('calibration', 'holdout') for label in (True, False))):
        raise ValueError('need_100_split_balanced_cases')


def case_identity(data):
    """Bind all authored fields while permitting post-collection label review."""
    return packages.canonical_hash({**data, 'cases': [
        {k: v for k, v in case.items() if k != 'candidate_labels'} for case in data['cases']]})
