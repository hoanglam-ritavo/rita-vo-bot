"""Private, source-reviewed paired retrieval evaluation; no model/DB imports."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import unicodedata
from uuid import UUID

from src.paths import REPOSITORY_ROOT


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def private_path(value):
    path = Path(value)
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    path = path.resolve()
    root = (REPOSITORY_ROOT / '.tmp').resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('semantic_evaluation_path_outside_tmp')
    return path


def validate_dataset(data, *, collect_only=False):
    """Validate declared coverage/split isolation, never certify semantic review."""
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('invalid_semantic_evaluation_dataset')
    if not collect_only and (data.get('reviewed') is not True
            or not isinstance(data.get('review_notes'), str) or not data['review_notes'].strip()):
        raise ValueError('human_review_required')
    units = data.get('required_units')
    if (not isinstance(units, list) or not units or any(not isinstance(u, str) or not u.strip() for u in units)
            or len(units) != len(set(units))):
        raise ValueError('invalid_required_units')
    cases = data.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('empty_semantic_evaluation_cases')
    ids, questions, families, covered = set(), set(), {}, set()
    wording_coverage = defaultdict(set)
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError('invalid_semantic_evaluation_case')
        for key in ('id', 'question', 'domain', 'intent_group'):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError('invalid_semantic_evaluation_case')
        question = ' '.join(unicodedata.normalize('NFC', case['question']).casefold().split())
        if case['id'] in ids or question in questions or len(case['question'].strip()) > 8000:
            raise ValueError('duplicate_or_invalid_semantic_case')
        ids.add(case['id'])
        questions.add(question)
        split = case.get('split')
        if split not in ('calibration', 'holdout', 'regression'):
            raise ValueError('invalid_semantic_case_split')
        family = case['intent_group']
        if family in families and families[family] != split:
            raise ValueError('semantic_family_split_leak')
        families[family] = split
        for key in ('relevant', 'critical', 'top1_required'):
            if type(case.get(key)) is not bool:
                raise ValueError('semantic_case_flags_required')
        case_units = case.get('unit_ids')
        if (not isinstance(case_units, list) or len(case_units) != len(set(case_units))
                or any(u not in units for u in case_units) or case['relevant'] and not case_units):
            raise ValueError('invalid_semantic_case_units')
        covered.update(case_units)
        wording = case.get('wording')
        if not collect_only and wording not in ('direct', 'paraphrase', 'contrast', 'control'):
            raise ValueError('semantic_wording_required')
        if case['relevant']:
            for unit in case_units:
                wording_coverage[unit].add(wording)
        groups = case.get('required_groups')
        if not isinstance(groups, list) or case['relevant'] != bool(groups):
            raise ValueError('invalid_semantic_evidence_groups')
        for group in groups:
            if not isinstance(group, list) or not group:
                raise ValueError('invalid_semantic_evidence_groups')
            for ref in group:
                if not isinstance(ref, dict) or set(ref) != {'document_id', 'revision_id', 'chunk_index'}:
                    raise ValueError('invalid_semantic_evidence_reference')
                try:
                    UUID(ref['document_id'])
                    UUID(ref['revision_id'])
                except (ValueError, TypeError, AttributeError):
                    raise ValueError('invalid_semantic_evidence_reference') from None
                if type(ref['chunk_index']) is not int or ref['chunk_index'] < 0:
                    raise ValueError('invalid_semantic_evidence_reference')
        labels = case.get('candidate_labels', {})
        if not isinstance(labels, dict) or any(type(value) is not bool for value in labels.values()):
            raise ValueError('invalid_candidate_labels')
        for chunk_id in labels:
            try:
                UUID(chunk_id)
            except (ValueError, TypeError, AttributeError):
                raise ValueError('invalid_candidate_labels') from None
    if not collect_only:
        if covered != set(units):
            raise ValueError('incomplete_semantic_unit_coverage')
        if any(not {'direct', 'paraphrase'} <= wording_coverage[unit] for unit in units):
            raise ValueError('semantic_direct_and_paraphrase_required')
        if any(not any(c['critical'] for c in cases if c['split'] == split)
               for split in ('calibration', 'holdout')):
            raise ValueError('semantic_critical_cases_required')
        if (len(cases) < 300 or sum(c['split'] == 'regression' for c in cases) < 20
                or any(sum(c['split'] == split and c['relevant'] is label for c in cases) < 20
                       for split in ('calibration', 'holdout') for label in (True, False))):
            raise ValueError('need_300_split_balanced_cases')
    return cases


def check_labels(case, results):
    """One source label per identity across the union of all pipeline candidates."""
    expected = {m['chunk_id'] for result in results.values() for m in result['matches']}
    if set(case.get('candidate_labels', {})) != expected:
        raise ValueError('complete_union_candidate_labels_required')


def evidence(case, matches):
    """AND of groups, OR within a group; supports evidence spanning documents."""
    def identity(match):
        return match['document_id'], match['revision_id'], match['chunk_index']
    ranks = {identity(match): rank for rank, match in reversed(list(enumerate(matches, 1)))}
    group_ranks = [min((ranks[identity(ref)] for ref in group if identity(ref) in ranks), default=None)
                   for group in case['required_groups']]
    return {'group_ranks': group_ranks,
            'complete': all(rank is not None for rank in group_ranks) if case['relevant'] else None,
            'group_recall': sum(rank is not None for rank in group_ranks) / len(group_ranks)
                            if group_ranks else None}


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def threshold_for(cases, results):
    validate_scores(results)
    negatives = [m['rerank_score'] for case, result in zip(cases, results)
                 if case['split'] == 'calibration' for m in result['matches']
                 if not case['candidate_labels'][m['chunk_id']]]
    if not negatives or any(type(score) not in (float, int) or not math.isfinite(score) for score in negatives):
        return None
    threshold = math.nextafter(max(negatives), math.inf)
    return threshold if math.isfinite(threshold) else None


def validate_scores(results):
    if any(type(match.get('rerank_score')) not in (float, int)
           or not math.isfinite(match['rerank_score']) for result in results for match in result['matches']):
        raise ValueError('invalid_semantic_rerank_scores')


def metrics(case, result, threshold):
    validate_scores([result])
    candidates = result['matches']
    labels = case['candidate_labels']
    retained = [m for m in candidates if threshold is not None and m['rerank_score'] >= threshold]
    selected = retained[:5]
    before = result.get('candidate_order')
    if not isinstance(before, list):
        raise ValueError('candidate_diagnostics_required')
    # candidate_order intentionally contains IDs/index/revision/run but not document_id.
    records = {m['chunk_id']: m for m in candidates}
    if set(records) != {m['chunk_id'] for m in before}:
        raise ValueError('invalid_candidate_diagnostics')
    candidate_grade = evidence(case, [records[m['chunk_id']] for m in before])
    selected_grade = evidence(case, selected)
    raw_grade = evidence(case, candidates[:5])
    dcg = sum(int(labels[m['chunk_id']]) / math.log2(rank + 1)
              for rank, m in enumerate(selected, 1))
    ideal_count = min(5, sum(labels.values()))
    idcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    normalized_content = [' '.join(unicodedata.normalize('NFC', m.get('content', '')).split())
                          for m in selected]
    duplicate_count = len(normalized_content) - len(set(normalized_content))
    return {'id': case['id'], 'domain': case['domain'], 'split': case['split'],
            'intent_group': case['intent_group'], 'relevant': case['relevant'],
            'critical': case['critical'], 'top1_required': case['top1_required'],
            'candidate_group_recall30': candidate_grade['group_recall'],
            'candidate_complete30': candidate_grade['complete'],
            'complete5': selected_grade['complete'], 'group_ranks': selected_grade['group_ranks'],
            'ndcg5': dcg / idcg if idcg else 0. if case['relevant'] else None,
            'unfiltered_complete5': raw_grade['complete'], 'unfiltered_group_ranks': raw_grade['group_ranks'],
            'unfiltered_top1_relevant': bool(candidates) and labels[candidates[0]['chunk_id']],
            'top1_relevant': bool(selected) and labels[selected[0]['chunk_id']],
            'required_top1_passed': 1 in selected_grade['group_ranks'],
            'retained5': len(selected), 'irrelevant20': sum(not labels[m['chunk_id']] for m in retained[:20]),
            'duplicate5': duplicate_count, 'false_empty': case['relevant'] and not selected,
            'fallback_reason': 'no_eligible_documents' if result['status'] == 'no_eligible_documents' else
                               'no_candidates' if not candidates else
                               'relevance_unavailable' if threshold is None else
                               'below_relevance_threshold' if not selected else None}


def _mean(values):
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


def summarize_rows(rows):
    supported = [row for row in rows if row['relevant']]
    families = defaultdict(list)
    for row in supported:
        families[row['intent_group']].append(row)
    comparable = [group for group in families.values() if len(group) > 1]
    return {'cases': len(rows), 'supported': len(supported),
            'candidate_recall30': _mean(row['candidate_group_recall30'] for row in supported),
            'candidate_complete30': _mean(row['candidate_complete30'] for row in supported),
            'complete5': _mean(row['complete5'] for row in supported),
            'unfiltered_complete5': _mean(row['unfiltered_complete5'] for row in supported),
            'unfiltered_top1_relevant': _mean(row['unfiltered_top1_relevant'] for row in supported),
            'ndcg5': _mean(row['ndcg5'] for row in supported),
            'top1_relevant': _mean(row['top1_relevant'] for row in supported),
            'false_empty_count': sum(row['false_empty'] for row in rows),
            'irrelevant20': sum(row['irrelevant20'] for row in rows),
            'unsupported_acceptances': sum(row['retained5'] > 0 for row in rows if not row['relevant']),
            'duplicate5': sum(row['duplicate5'] for row in rows),
            'paraphrase_families': len(comparable),
            'paraphrase_consistency': _mean(len({(r['complete5'], r['top1_relevant']) for r in group}) == 1
                                            for group in comparable)}


def summarize(cases, results, durations, threshold):
    rows = [metrics(case, result, threshold) for case, result in zip(cases, results)]
    domains = {domain: summarize_rows([row for row in rows if row['domain'] == domain])
               for domain in sorted({row['domain'] for row in rows})}
    holdout_domains = {domain: summarize_rows([row for row in rows
                        if row['domain'] == domain and row['split'] == 'holdout'])
                       for domain in domains}
    splits = {split: summarize_rows([row for row in rows if row['split'] == split])
              for split in ('calibration', 'holdout', 'regression')}
    summary = summarize_rows(rows)
    summary.update(domains=domains, holdout_domains=holdout_domains, splits=splits,
        macro_ndcg5=_mean(d['ndcg5'] for d in domains.values()),
        holdout_macro_ndcg5=_mean(d['ndcg5'] for d in holdout_domains.values()),
        worst_domain_complete5=min((d['complete5'] for d in domains.values() if d['complete5'] is not None), default=None),
        p50_seconds=percentile(durations, .5), p95_seconds=percentile(durations, .95), latency_requests=len(durations))
    gates = {'calibration_available': threshold is not None,
             'complete_evidence': all(splits[split]['complete5'] is not None and splits[split]['complete5'] >= .9
                                      for split in ('calibration', 'holdout')),
             'no_irrelevant_returned': all(row['irrelevant20'] == 0 for row in rows),
             'no_unsupported_acceptances': all(row['retained5'] == 0 for row in rows if not row['relevant']),
             'critical': all(row['complete5'] is True for row in rows if row['critical'] and row['relevant'])
                         and all(row['retained5'] == 0 for row in rows if row['critical'] and not row['relevant']),
             'required_top1': all(row['required_top1_passed'] for row in rows if row['top1_required']),
             'latency': len(durations) >= 100 and summary['p95_seconds'] <= 5}
    return {'cases': rows, 'summary': summary, 'threshold': threshold, 'gates': gates,
            'absolute_gates_passed': all(gates.values())}


def compare(reference, candidate):
    """Do not mask a domain regression behind a large-topic average."""
    before, after = reference['summary'], candidate['summary']
    a, b = before['holdout_macro_ndcg5'], after['holdout_macro_ndcg5']
    gain = a is not None and b is not None and b > a
    overall = (before['complete5'] is not None and after['complete5'] is not None
               and after['complete5'] >= before['complete5'])
    def not_reduced(old_domains, new_domains):
        return set(old_domains) == set(new_domains) and all(
            old['complete5'] is None or new_domains[name]['complete5'] is not None
            and new_domains[name]['complete5'] >= old['complete5'] for name, old in old_domains.items())
    domains = not_reduced(before['domains'], after['domains'])
    holdout_domains = not_reduced(before['holdout_domains'], after['holdout_domains'])
    return {'holdout_macro_ndcg5_improved': gain, 'complete_evidence_not_reduced': overall,
            'no_domain_evidence_regression': domains,
            'no_holdout_domain_evidence_regression': holdout_domains,
            'promotion_ready': candidate['absolute_gates_passed'] and gain and overall and domains and holdout_domains}
