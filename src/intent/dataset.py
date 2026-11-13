"""Reviewed intent datasets and private, non-overwriting experiment artifacts."""
from collections import Counter
import json
from pathlib import Path
import unicodedata

from src.intent.runtime import SOURCES, STYLES, digest
from src.paths import REPOSITORY_ROOT

SAFE_ERRORS = frozenset({'human_review_required', 'invalid_intent_dataset', 'need_600_cases',
    'invalid_intent_case', 'duplicate_or_invalid_case', 'invalid_intent_label', 'critical_flag_required',
    'invalid_style_labels', 'intent_group_split_leak', 'insufficient_split_coverage',
    'critical_cases_required', 'insufficient_training_groups', 'style_negative_examples_required',
    'intent_path_outside_tmp', 'intent_output_exists_or_inside_model', 'training_fold_missing_class',
    'model_changed', 'dataset_changed', 'training_inputs_changed', 'intent_quality_not_accepted',
    'calibrated_reranking_and_200_requests_required', 'retrieval_calibration_unavailable',
    'benchmark_inputs_changed', 'intent_artifact_incompatible', 'invalid_intent_weights',
    'invalid_intent_threshold', 'question_exceeds_model_limit'})


def safe_code(exc, fallback):
    code = str(exc)
    return code if code in SAFE_ERRORS else fallback


def private_path(value):
    path = Path(value).resolve()
    root = (REPOSITORY_ROOT / '.tmp').resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('intent_path_outside_tmp')
    return path


def read_json(path):
    return json.loads(private_path(path).read_text(encoding='utf-8'))


def write_new(path, value):
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def check_outputs(prefix, suffixes, model_path):
    model = Path(model_path).resolve()
    for suffix in suffixes:
        path = private_path(str(prefix) + suffix)
        if path.exists() or path.is_relative_to(model):
            raise ValueError('intent_output_exists_or_inside_model')



def dataset_hash(data):
    return digest(data)


def inspect_dataset(data):
    """Aggregate independent structural errors without echoing submitted text/IDs."""
    errors = []
    counts = {}
    def error(code, index=None, **fields):
        errors.append({'code': code, **({'case_index': index} if index is not None else {}), **fields})
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data.get('schema_version') != 1:
        error('invalid_intent_dataset')
    if not isinstance(data, dict):
        data = {}
    reviewed = data.get('reviewed') is True and isinstance(data.get('review_notes'), str) and bool(data['review_notes'].strip())
    cases = data.get('cases')
    if not isinstance(cases, list):
        error('invalid_intent_dataset')
        cases = []
    if len(cases) < 600:
        error('need_600_cases')
    ids, questions, groups = set(), set(), {}
    valid_rows = []
    for index, case in enumerate(cases):
        before = len(errors)
        if not isinstance(case, dict):
            error('invalid_intent_case', index)
            continue
        for key in ('id', 'question', 'intent_group'):
            if not isinstance(case.get(key), str) or not case[key].strip():
                error('invalid_intent_case', index, field=key)
        if isinstance(case.get('id'), str):
            if case['id'] in ids:
                error('duplicate_or_invalid_case', index, field='id')
            ids.add(case['id'])
        if isinstance(case.get('question'), str):
            question = ' '.join(unicodedata.normalize('NFC', case['question']).casefold().split())
            if len(case['question'].strip()) > 8000 or question in questions:
                error('duplicate_or_invalid_case', index, field='question')
            questions.add(question)
        source, split = case.get('source'), case.get('split')
        if source not in SOURCES or split not in ('train', 'calibration', 'holdout'):
            error('invalid_intent_label', index)
        if type(case.get('critical')) is not bool:
            error('critical_flag_required', index)
        styles = case.get('styles')
        styles_valid = (isinstance(styles, list) and all(isinstance(s, str) and s in STYLES for s in styles))
        if (not styles_valid or len(styles) != len(set(styles))
                or bool(styles) != (source in ('type_a', 'mixed'))):
            error('invalid_style_labels', index)
        group = case.get('intent_group')
        if isinstance(group, str) and isinstance(split, str):
            if group in groups and groups[group] != split:
                error('intent_group_split_leak', index)
            groups[group] = split
        if before == len(errors):
            valid_rows.append(case)
    for split, minimum, style_min in [('train', 60, 40), ('calibration', 20, 15), ('holdout', 20, 15)]:
        rows = [c for c in valid_rows if c['split'] == split]
        sources = Counter(c['source'] for c in rows)
        styles = Counter(s for c in rows for s in c['styles'])
        counts[split] = {'sources': {s: sources[s] for s in SOURCES}, 'styles': {s: styles[s] for s in STYLES}}
        if any(sources[s] < minimum for s in SOURCES) or any(styles[s] < style_min for s in STYLES):
            error('insufficient_split_coverage', split=split)
        a_count = sum(c['source'] in ('type_a', 'mixed') for c in rows)
        if any(styles[s] == a_count for s in STYLES):
            error('style_negative_examples_required', split=split)
        if split != 'train' and not any(c['critical'] for c in rows):
            error('critical_cases_required', split=split)
    if any(len({c['intent_group'] for c in valid_rows if c['split'] == 'train' and c['source'] == label}) < 3
           for label in SOURCES):
        error('insufficient_training_groups')
    return {'status': 'valid' if not errors else 'invalid', 'case_count': len(cases),
            'review_required': not reviewed, 'training_allowed': reviewed and not errors,
            'counts': counts, 'errors': errors,
            'limitations': ['semantic_labels_and_undeclared_group_leakage_not_verified']}


def validate_dataset(data, *, require_review=True):
    report = inspect_dataset(data)
    # Preserve the old training entrypoint's review-first rejection.
    if require_review and report['review_required']:
        raise ValueError('human_review_required')
    if report['errors']:
        raise ValueError(report['errors'][0]['code'])
    return data['cases']
