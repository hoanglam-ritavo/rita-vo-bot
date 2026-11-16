"""Manual source-review gates; model grades cannot grant acceptance."""
from collections import Counter
import math
import json
from pathlib import Path

from .identity import MODEL_ID, DEADLINE_SECONDS, implementation_identity, retrieval_identity, sha_file

CATEGORIES = {'supported': 20, 'partial': 10, 'absent': 10, 'ambiguous': 10, 'adversarial': 10}


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
        'latency': sum(t <= 30 and r.get('error') is None for t, r in zip(timings, rows)) / 60 >= .95,
        'memory': memory <= 85,
        'value': sum(bool(r.get('accepted')) and grades[r['id']]['more_useful_than_excerpts'] for r in supported) / 10 >= .8,
        'real_runtime': report.get('runtime_measured') is True,
        'user_acceptance': reviews.get('user_accepted') is True,
    }
    return {'gates': gates, 'integration_ready': all(gates.values()),
            'notice': 'Manual source review and user acceptance are not model-verifier guarantees.'}


def load_acceptance(report_path, review_path, settings, runtime_identity):
    """Recompute gates from hash-bound report/review; a standalone true flag grants nothing."""
    report_path, review_path = Path(report_path), Path(review_path)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    review = json.loads(review_path.read_text(encoding='utf-8'))
    if (report.get('schema_version') != 3 or report.get('status') != 'awaiting_manual_review'
            or report.get('error') is not None
            or review.get('report_sha256') != sha_file(report_path)
            or report.get('samples_sha256') != sha_file(report_path.with_name('samples.private.json'))
            or not evaluate_gates(report, review)['integration_ready']):
        raise ValueError('answer_acceptance_required')
    if (report.get('runtime_identity') != runtime_identity
            or report.get('implementation_sha256') != implementation_identity()
            or report.get('retrieval_identity') != retrieval_identity(settings)
            or report.get('configuration') != configuration()
            or not isinstance(report.get('corpus_fingerprint'), str)
            or not report['corpus_fingerprint']):
        raise ValueError('answer_acceptance_stale')
    return report['corpus_fingerprint']


def configuration():
    return {'model': MODEL_ID, 'context': 8192, 'threads': 6, 'generation_tokens': 1024,
            'verification_tokens': 512, 'thinking': False, 'gpu_layers': 0,
            'deadline_seconds': DEADLINE_SECONDS, 'temperature': 0, 'seed': 42, 'top_k': 5,
            'backend': 'llama-cpp-python-inprocess', 'top_p': 1., 'min_p': 0., 'repeat_penalty': 1.}
