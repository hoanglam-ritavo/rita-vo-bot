"""Development-only local logistic fitting; never emits an accepted profile.

Input rows contain feature vectors and source-reviewed labels, not text. Five
intent-family folds keep normalization, fitting and threshold selection separate
from each validation fold. Calibration and holdout inputs are rejected.
"""
from collections import defaultdict
import hashlib
import json
import math

from .scoring import FEATURES, apply_scoring, scoring_digest, validate_scoring

VERSION = 'passage-logistic-v1'
LAMBDAS = (0.01, 0.1, 1.0, 10.0)
FOLDS = 5
GROUP_SEED = 'relevance-development-folds-v1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def validate_development(data):
    """Validate declarations and vectors; cannot establish actual human review."""
    if (not isinstance(data, dict) or type(data.get('schema_version')) is not int
            or data['schema_version'] != 1 or data.get('split') != 'development'):
        raise ValueError('development_only_scoring_data_required')
    if (data.get('reviewed') is not True or not isinstance(data.get('review_notes'), str)
            or not data['review_notes'].strip()):
        raise ValueError('development_source_review_required')
    if (data.get('version', VERSION) != VERSION
            or data.get('feature_names', list(FEATURES)) != list(FEATURES)):
        raise ValueError('scoring_feature_version_mismatch')
    rows = data.get('rows')
    if not isinstance(rows, list) or not rows:
        raise ValueError('empty_development_rows')
    seen, cases, units, labels = set(), {}, {}, {}
    cleaned = []
    for row in rows:
        if not isinstance(row, dict) or row.get('split', 'development') != 'development':
            raise ValueError('development_only_scoring_data_required')
        if any(not isinstance(row.get(k), str) or not row[k].strip()
               for k in ('case_id', 'intent_group', 'arm', 'chunk_id')):
            raise ValueError('invalid_development_identity')
        if type(row.get('relevant')) is not bool:
            raise ValueError('boolean_development_label_required')
        features = row.get('features')
        if (not isinstance(features, list) or len(features) != len(FEATURES)
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in features)):
            raise ValueError('invalid_development_features')
        groups = row.get('required_groups')
        if (not isinstance(groups, list) or any(not isinstance(g, list) or not g
                or any(not isinstance(c, str) or not c for c in g)
                or len(g) != len(set(g)) for g in groups)):
            raise ValueError('invalid_development_evidence_groups')
        key = row['case_id'], row['arm'], row['chunk_id']
        if key in seen:
            raise ValueError('duplicate_development_candidate')
        seen.add(key)
        label_key = row['case_id'], row['chunk_id']
        if label_key in labels and labels[label_key] is not row['relevant']:
            raise ValueError('inconsistent_development_union_label')
        labels[label_key] = row['relevant']
        identity = row['intent_group'], groups
        if row['case_id'] in cases and cases[row['case_id']] != identity:
            raise ValueError('inconsistent_development_case')
        cases[row['case_id']] = identity
        unit = row['case_id'], row['arm']
        if unit in units and units[unit] != groups:
            raise ValueError('inconsistent_development_evidence_groups')
        units[unit] = groups
        # Missing required references are valid candidate-pool misses, not a
        # reason to omit a hard case from training/validation diagnostics.
        if any(row['chunk_id'] in g for g in groups) and not row['relevant']:
            raise ValueError('required_evidence_labeled_irrelevant')
        if not groups and row['relevant']:
            raise ValueError('unsupported_case_has_relevant_candidate')
        cleaned.append({k: row[k] for k in
            ('case_id', 'intent_group', 'arm', 'chunk_id', 'features', 'relevant', 'required_groups')})
    if len(cases) < 300:
        raise ValueError('need_300_development_cases')
    arms = {row['arm'] for row in cleaned}
    if any({arm for case, arm in units if case == identity} != arms for identity in cases):
        raise ValueError('development_arms_not_paired')
    groups = {row['intent_group'] for row in cleaned}
    if len(groups) < FOLDS:
        raise ValueError('need_five_development_families')
    return sorted(cleaned, key=lambda r: (r['case_id'], r['arm'], r['chunk_id']))


def _weights(rows):
    """Each arm/case has equal total weight, split equally by available class."""
    import numpy as np
    units = defaultdict(lambda: defaultdict(list))
    for index, row in enumerate(rows):
        units[row['case_id'], row['arm']][row['relevant']].append(index)
    result = np.zeros(len(rows), dtype=np.float64)
    for classes in units.values():
        for indices in classes.values():
            result[indices] = 1.0 / (len(units) * len(classes) * len(indices))
    return result


def _fit(rows, penalty):
    import numpy as np
    x = np.asarray([r['features'] for r in rows], dtype=np.float64)
    y = np.asarray([r['relevant'] for r in rows], dtype=np.float64)
    if len(set(y)) != 2:
        raise ValueError('both_development_labels_required_in_every_fold')
    sample = _weights(rows)
    means = np.sum(x * sample[:, None], axis=0)
    scales = np.sqrt(np.sum((x - means) ** 2 * sample[:, None], axis=0))
    scales[scales < 1e-12] = 1.0
    design = np.column_stack(((x - means) / scales, np.ones(len(x))))
    if not np.isfinite(design).all():
        raise ValueError('invalid_development_normalization')
    coefficients = np.zeros(len(FEATURES) + 1, dtype=np.float64)
    regularization = np.asarray([penalty] * len(FEATURES) + [0.0])

    def objective(value):
        z = design @ value
        return float(np.sum(sample * (np.logaddexp(0, z) - y * z))
                     + 0.5 * np.sum(regularization * value ** 2))

    converged = False
    for iteration in range(100):
        z = design @ coefficients
        probability = np.exp(-np.logaddexp(0, -z))
        gradient = design.T @ (sample * (probability - y)) + regularization * coefficients
        if np.max(np.abs(gradient)) <= 1e-9:
            converged = True
            break
        hessian = (design.T * (sample * probability * (1 - probability))) @ design
        hessian += np.diag(regularization)
        try:
            direction = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            raise ValueError('scoring_fit_singular') from None
        loss, step = objective(coefficients), 1.0
        slope = float(gradient @ direction)
        if not np.isfinite(direction).all() or not math.isfinite(loss) or slope <= 0:
            raise ValueError('scoring_fit_nonfinite')
        for _ in range(40):
            proposed = coefficients - step * direction
            if objective(proposed) <= loss - 1e-4 * step * slope:
                coefficients = proposed
                break
            step *= 0.5
        else:
            raise ValueError('scoring_fit_line_search_failed')
    if not converged or not np.isfinite(coefficients).all():
        raise ValueError('scoring_fit_not_converged')
    result = {'version': VERSION, 'feature_names': list(FEATURES),
        'means': means.tolist(), 'scales': scales.tolist(),
        'weights': coefficients[:-1].tolist(), 'bias': float(coefficients[-1])}
    result['digest'] = scoring_digest(result)
    return validate_scoring(result), iteration + 1


def _threshold(rows, scores):
    negative = [s for r, s in zip(rows, scores) if not r['relevant']]
    if not negative:
        raise ValueError('both_development_labels_required_in_every_fold')
    value = math.nextafter(max(negative), math.inf)
    if not math.isfinite(value):
        raise ValueError('invalid_development_threshold')
    return value


def _measure(rows, scores, threshold):
    grouped = defaultdict(list)
    for row, score in zip(rows, scores):
        grouped[row['case_id'], row['arm']].append((row, score))
    positives = complete = irrelevant = unsupported = 0
    for values in grouped.values():
        groups = values[0][0]['required_groups']
        selected = sorted(((r, s) for r, s in values if s >= threshold),
                          key=lambda item: (-item[1], item[0]['chunk_id']))
        irrelevant += sum(not r['relevant'] for r, _ in selected[:20])
        if groups:
            positives += 1
            ids = {r['chunk_id'] for r, _ in selected[:5]}
            complete += all(any(identity in ids for identity in group) for group in groups)
        else:
            unsupported += bool(selected)
    return {'positive_case_arms': positives, 'complete_case_arms': complete,
        'complete_recall5': complete / positives if positives else 0.0,
        'irrelevant_returned20': irrelevant, 'unsupported_acceptances': unsupported}


def fit_development(data):
    """Return candidate coefficients and development diagnostics, never approval."""
    rows = validate_development(data)
    groups = sorted({r['intent_group'] for r in rows},
        key=lambda g: (hashlib.sha256((GROUP_SEED + ':' + g).encode()).hexdigest(), g))
    assignments = {group: index % FOLDS for index, group in enumerate(groups)}
    trials = []
    for penalty in LAMBDAS:
        measured = []
        for fold in range(FOLDS):
            train = [r for r in rows if assignments[r['intent_group']] != fold]
            validation = [r for r in rows if assignments[r['intent_group']] == fold]
            if {r['relevant'] for r in validation} != {True, False}:
                raise ValueError('both_development_labels_required_in_every_fold')
            scoring, iterations = _fit(train, penalty)
            threshold = _threshold(train, apply_scoring([r['features'] for r in train], scoring))
            metrics = _measure(validation, apply_scoring([r['features'] for r in validation], scoring), threshold)
            measured.append({'fold': fold, 'train_rows': len(train), 'validation_rows': len(validation),
                'normalization_training_only': True, 'threshold_training_only': True,
                'normalization_means': scoring['means'], 'normalization_scales': scoring['scales'],
                'threshold': threshold, 'iterations': iterations, **metrics})
        total = sum(f['positive_case_arms'] for f in measured)
        false_count = sum(f['irrelevant_returned20'] + f['unsupported_acceptances'] for f in measured)
        trials.append({'l2': penalty, 'folds': measured,
            'zero_false_validation': false_count == 0,
            'validation_false_count': false_count,
            'validation_complete_recall5': sum(f['complete_case_arms'] for f in measured) / total if total else 0.0})
    # False-free candidates dominate. Otherwise keep an explicitly unaccepted
    # research candidate with the fewest validation errors, never a profile.
    selected = max(trials, key=lambda t: (t['zero_false_validation'],
        -t['validation_false_count'], t['validation_complete_recall5'], t['l2']))
    scoring, iterations = _fit(rows, selected['l2'])
    report = {'schema_version': 1, 'status': 'development_candidate_only',
        'accepted_profile': False, 'holdout_evaluated': False, 'source_review_declared': True,
        'training_only': True, 'dataset_sha256': _digest(data),
        'data_sha256': _digest(data), 'normalized_rows_sha256': _digest(rows),
        'development_intent_groups': sorted(groups),
        'development_case_ids': sorted({r['case_id'] for r in rows}),
        'feature_names': list(FEATURES), 'rows': len(rows),
        'cases': len({r['case_id'] for r in rows}), 'families': len(groups),
        'arms': sorted({r['arm'] for r in rows}), 'group_seed': GROUP_SEED,
        'fold_assignments_sha256': _digest(assignments),
        'fold_family_counts': [sum(v == f for v in assignments.values()) for f in range(FOLDS)],
        'fold_assignments': assignments, 'trials': trials, 'selected_l2': selected['l2'],
        'selected_validation_zero_false': selected['zero_false_validation'],
        'selected_validation_complete_recall5': selected['validation_complete_recall5'],
        'final_fit_iterations': iterations, 'scoring_digest': scoring['digest'],
        'selection': 'zero_false_then_fewest_errors_then_complete_recall_then_stronger_l2',
        'weights': 'equal_case_arm_then_equal_present_class'}
    return scoring, report
