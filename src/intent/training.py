"""Train/calibrate/evaluate frozen E5 intent heads. No DB, downloads, or publication.

All inputs/outputs except the supplied model directory must live under .tmp.
The output is a candidate, never a runtime-approved artifact; benchmark_intent
must independently establish retrieval invariance and latency before promotion.
"""
import argparse
import json
import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, confusion_matrix, precision_score
from sklearn.model_selection import StratifiedGroupKFold
import sklearn

from src.intent.dataset import read_json, write_new, validate_dataset, dataset_hash, check_outputs, safe_code
from src.intent.runtime import SOURCES, STYLES, scores


def fit_head(x, y, groups, *, binary=False):
    """Training-only model selection; calibration/holdout never choose C."""
    splits = list(StratifiedGroupKFold(3, shuffle=True, random_state=42).split(x, y, groups))
    best, best_c = -1., None
    for c in (.1, 1., 10.):
        values = []
        for train, test in splits:
            model = LogisticRegression(C=c, solver='lbfgs', max_iter=2000, random_state=42)
            with warnings.catch_warnings():
                warnings.simplefilter('error', ConvergenceWarning)
                model.fit(x[train], y[train])
            if len(model.classes_) != (2 if binary else len(SOURCES)):
                raise ValueError('training_fold_missing_class')
            values.append(f1_score(y[test], model.predict(x[test]), average='binary' if binary else 'macro',
                                   zero_division=0))
        mean = float(np.mean(values))
        if mean > best:
            best, best_c = mean, c
    model = LogisticRegression(C=best_c, solver='lbfgs', max_iter=2000, random_state=42)
    with warnings.catch_warnings():
        warnings.simplefilter('error', ConvergenceWarning)
        model.fit(x, y)
    return model, best_c


def accepted_mask(probabilities, threshold, margin):
    ordered = np.sort(probabilities, axis=1)
    return (ordered[:, -1] >= threshold) & (ordered[:, -1] - ordered[:, -2] >= margin)


def source_metrics(y, probabilities, threshold, margin):
    predicted = probabilities.argmax(axis=1)
    accepted = accepted_mask(probabilities, threshold, margin)
    precisions = precision_score(y[accepted], predicted[accepted], labels=range(6),
                                 average=None, zero_division=0)
    return {'macro_f1': float(f1_score(y, predicted, labels=range(6), average='macro', zero_division=0)),
            'coverage': float(accepted.mean()), 'accepted_precision': precisions.tolist(),
            'confusion_matrix': confusion_matrix(y, predicted, labels=range(6)).tolist()}


def calibrate(y, probabilities):
    choices = []
    predicted = probabilities.argmax(axis=1)
    for threshold in np.linspace(0, 1, 101):
        for margin in np.linspace(0, 1, 51):
            accepted = accepted_mask(probabilities, threshold, margin)
            coverage = float(accepted.mean())
            if coverage < .8:
                continue
            counts = np.bincount(predicted[accepted], minlength=6)
            correct = np.bincount(predicted[accepted & (predicted == y)], minlength=6)
            if np.all(counts > 0) and np.all(correct / np.maximum(counts, 1) >= .9):
                choices.append((coverage, -float(threshold), -float(margin)))
    if not choices:
        return 1., 1., False
    _, threshold, margin = max(choices)
    return -threshold, -margin, True


def evaluate(artifact, x, cases):
    source, style = scores(artifact, x)
    y = np.array([SOURCES.index(c['source']) for c in cases])
    truth = np.array([[int(s in c['styles']) for s in STYLES] for c in cases])
    accepted = accepted_mask(source, artifact['source_threshold'], artifact['source_margin'])
    predicted = source.argmax(axis=1)
    a_mask = np.isin(y, [0, 2])
    style_pred = style >= artifact['style_thresholds']
    # End-to-end: a wrong route or abstention must not receive oracle style credit.
    style_pred[~(accepted & np.isin(predicted, [0, 2]))] = False
    measured = source_metrics(y, source, artifact['source_threshold'], artifact['source_margin'])
    measured['style_f1'] = float(f1_score(truth[a_mask], style_pred[a_mask], average='macro', zero_division=0))
    measured['per_style_f1'] = f1_score(truth[a_mask], style_pred[a_mask], average=None, zero_division=0).tolist()
    critical = np.array([c['critical'] for c in cases])
    # Conservative: any accepted incorrect critical route fails, including A/B/mixed confusion.
    measured['critical_wrong'] = int(np.sum(critical & accepted & (predicted != y)))
    measured['critical_uncertain'] = int(np.sum(critical & ~accepted))
    rows = [{'id': c['id'], 'expected': c['source'], 'predicted': SOURCES[predicted[i]],
             'status': 'classified' if accepted[i] else 'uncertain',
             'source_scores': source[i].tolist(), 'style_scores': style[i].tolist(),
             'expected_styles': c['styles'], 'predicted_styles': [s for j, s in enumerate(STYLES) if style_pred[i, j]]}
            for i, c in enumerate(cases)]
    return measured, rows


def train(x, cases, model_identity):
    indices = {split: np.array([i for i, c in enumerate(cases) if c['split'] == split])
               for split in ('train', 'calibration', 'holdout')}
    tr, cal, ho = (indices[s] for s in ('train', 'calibration', 'holdout'))
    labels = np.array([SOURCES.index(c['source']) for c in cases])
    styles = np.array([[int(s in c['styles']) for s in STYLES] for c in cases])
    groups = np.array([c['intent_group'] for c in cases])
    source, source_c = fit_head(x[tr], labels[tr], groups[tr])
    a_train = tr[np.isin(labels[tr], [0, 2])]
    heads, selected = [], []
    for j in range(5):
        head, c = fit_head(x[a_train], styles[a_train, j], groups[a_train], binary=True)
        heads.append(head)
        selected.append(c)
    artifact = {'schema_version': 1, 'identity': model_identity, 'source_labels': list(SOURCES),
                'style_labels': list(STYLES), 'source_weights': source.coef_.tolist(),
                'source_bias': source.intercept_.tolist(), 'style_weights': [h.coef_[0].tolist() for h in heads],
                'style_bias': [float(h.intercept_[0]) for h in heads], 'source_threshold': 1.,
                'source_margin': 1., 'style_thresholds': [.5] * 5}
    p, style = scores(artifact, x)
    parity = np.max(np.abs(p - source.predict_proba(x))) <= 1e-6
    parity = bool(parity and all(np.max(np.abs(style[:, j] - h.predict_proba(x)[:, 1])) <= 1e-6
                               for j, h in enumerate(heads)))
    threshold, margin, calibrated = calibrate(labels[cal], p[cal])
    artifact.update(source_threshold=threshold, source_margin=margin)
    a_cal = cal[np.isin(labels[cal], [0, 2])]
    for j in range(5):
        options = [(f1_score(styles[a_cal, j], style[a_cal, j] >= t, zero_division=0), -float(t))
                   for t in np.linspace(.05, .95, 91)]
        artifact['style_thresholds'][j] = -max(options)[1]
    measurements, rows = {}, {}
    for split, idx in [('calibration', cal), ('holdout', ho)]:
        measurements[split], rows[split] = evaluate(artifact, x[idx], [cases[i] for i in idx])
    gates = {'source_f1': all(m['macro_f1'] >= .85 for m in measurements.values()),
             'accepted_precision': calibrated and all(min(m['accepted_precision']) >= .9 for m in measurements.values()),
             'coverage': all(m['coverage'] >= .8 for m in measurements.values()),
             'style_f1': all(m['style_f1'] >= .8 for m in measurements.values()),
             'critical': all(m['critical_wrong'] == 0 for m in measurements.values()),
             'parity': parity, 'latency': False}
    report = {'schema_version': 1, 'sklearn_version': sklearn.__version__,
              'selected_c': {'source': source_c, 'styles': selected}, 'measurements': measurements,
              'cases': rows, 'gates': gates, 'status': 'candidate_requires_benchmark'}
    return artifact, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--model-path', required=True)
    parser.add_argument('--model-revision', required=True)
    parser.add_argument('--rerank-preprocessing', action='store_true')
    parser.add_argument('--output-prefix', required=True)
    args = parser.parse_args(argv)
    from .preparation import OfflineEncoder, train_loaded, dependencies
    encoder = None
    try:
        check_outputs(args.output_prefix, ('-report.json', '-candidate.json'), args.model_path)
        data = read_json(args.dataset)
        validate_dataset(data)
        dependencies()
        encoder = OfflineEncoder({'model_path': args.model_path, 'model_revision': args.model_revision,
                                  'preprocessing': 'rerank_original' if args.rerank_preprocessing else 'vector_only'})
        encoder.load()
        if encoder.preflight(data['cases'])['status'] != 'passed':
            raise ValueError('question_exceeds_model_limit')
        encoder.verify()
        if dataset_hash(read_json(args.dataset)) != dataset_hash(data):
            raise ValueError('training_inputs_changed')
        artifact, report = train_loaded(encoder, data)
        encoder.verify()
        if dataset_hash(read_json(args.dataset)) != dataset_hash(data):
            raise ValueError('training_inputs_changed')
        encoder.close()
        encoder = None
        write_new(args.output_prefix + '-report.json', report)
        write_new(args.output_prefix + '-candidate.json', artifact)
        print(json.dumps({'status': report['status'], 'gates': report['gates']}))
        return 0 if all(v for k, v in report['gates'].items() if k != 'latency') else 1
    except Exception as exc:
        print(json.dumps({'status': 'error', 'code': safe_code(exc, 'intent_training_failed')}))
        return 2
    finally:
        if encoder is not None:
            encoder.close()



if __name__ == '__main__':
    raise SystemExit(main())
