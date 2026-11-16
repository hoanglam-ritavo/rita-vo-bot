"""Synthetic fitting checks; no policy labels, model inference or database IO."""
import copy
import json

import numpy as np
import pytest

from src.retrieval.scoring import FEATURES, apply_scoring, validate_scoring
from src.retrieval.scoring_training import fit_development, validate_development


def development():
    rows = []
    for case in range(300):
        supported = case % 2 == 0
        groups = [[f'positive-{case}']] if supported else []
        for arm in ('baseline', 'semantic'):
            for relevant in ((True, False) if supported else (False, False)):
                identity = f'positive-{case}' if relevant else f'negative-{case}-{len(rows)}'
                rows.append({'case_id': f'case-{case}', 'intent_group': f'family-{case % 10}',
                    'arm': arm, 'chunk_id': identity, 'required_groups': groups,
                    'features': [3.0 if relevant else -3.0, 0.0, 0.0, 1.0,
                                 0.8 if relevant else 0.1, 1.0, 1.0, 1.0],
                    'relevant': relevant})
    return {'schema_version': 1, 'split': 'development', 'reviewed': True,
            'review_notes': 'Synthetic reviewed fixture only.', 'feature_names': list(FEATURES), 'rows': rows}


def test_fitting_is_deterministic_development_only_and_runtime_compatible():
    data = development()
    # Each family needs both classes and supported/unsupported cases for useful
    # cross-validation; use case pairs rather than label-correlated families.
    for row in data['rows']:
        row['intent_group'] = 'family-' + str(int(row['case_id'].split('-')[1]) // 30)
    scoring, report = fit_development(data)
    validate_scoring(scoring)
    assert report['status'] == 'development_candidate_only'
    assert report['accepted_profile'] is False
    assert report['training_only'] is True
    assert report['holdout_evaluated'] is False
    assert report['cases'] == 300
    assert report['scoring_digest'] == scoring['digest']
    assert report['selected_validation_zero_false'] is True
    assert report['selected_validation_complete_recall5'] == 1.0
    assert sum(report['fold_family_counts']) == 10
    assert len(report['development_case_ids']) == 300
    scores = apply_scoring([r['features'] for r in data['rows']], scoring)
    assert min(s for r, s in zip(data['rows'], scores) if r['relevant']) > max(
        s for r, s in zip(data['rows'], scores) if not r['relevant'])
    reverse = copy.deepcopy(data)
    reverse['rows'].reverse()
    again, reordered = fit_development(reverse)
    assert again == scoring
    assert reordered['normalized_rows_sha256'] == report['normalized_rows_sha256']
    assert reordered['fold_assignments_sha256'] == report['fold_assignments_sha256']


def test_fold_normalization_uses_training_rows_only():
    data = development()
    for row in data['rows']:
        row['intent_group'] = 'family-' + str(int(row['case_id'].split('-')[1]) // 30)
        row['features'][1] = float(row['case_id'].split('-')[1])
    _, report = fit_development(data)
    assignments = report['fold_assignments']
    for fold in report['trials'][0]['folds']:
        train_case_ids = {r['case_id'] for r in data['rows']
                          if assignments[r['intent_group']] != fold['fold']}
        # The second feature is constant per case. Equal case/arm weighting
        # means its training normalization must equal this mean exactly.
        expected = np.mean([int(c.split('-')[1]) for c in train_case_ids])
        assert fold['normalization_means'][1] == pytest.approx(expected)
        assert fold['normalization_training_only'] is True
        assert fold['threshold_training_only'] is True


@pytest.mark.parametrize('location', ['top', 'row'])
def test_calibration_or_holdout_cannot_enter_training(location):
    data = development()
    if location == 'top':
        data['split'] = 'holdout'
    else:
        data['rows'][0]['split'] = 'calibration'
    with pytest.raises(ValueError, match='development_only'):
        fit_development(data)


@pytest.mark.parametrize('mutation,code', [
    (lambda d: d.update(reviewed=False), 'source_review'),
    (lambda d: d['rows'][0]['features'].__setitem__(0, float('nan')), 'features'),
    (lambda d: d['rows'][0]['features'].__setitem__(0, True), 'features'),
    (lambda d: d['rows'].append(copy.deepcopy(d['rows'][0])), 'duplicate'),
    (lambda d: d['rows'][0].update(relevant=False), 'required_evidence'),
    (lambda d: d['rows'].__delitem__(slice(0, 10)), '300_development'),
])
def test_invalid_or_unreviewed_development_inputs_are_rejected(mutation, code):
    data = development()
    mutation(data)
    with pytest.raises(ValueError, match=code):
        validate_development(data)


def test_missing_pool_evidence_remains_a_measured_failure():
    data = development()
    for row in data['rows']:
        row['intent_group'] = 'family-' + str(int(row['case_id'].split('-')[1]) // 30)
        if row['required_groups']:
            row['required_groups'] = [['not-in-candidate-pool']]
    _, report = fit_development(data)
    assert report['selected_validation_complete_recall5'] == 0.0
    assert report['accepted_profile'] is False


def test_relevance_is_one_source_label_across_arm_union():
    data = development()
    # Additional source-relevant context need not be in the required group, but
    # its label must agree when both arms return the same chunk UUID.
    data['rows'][3]['chunk_id'] = data['rows'][1]['chunk_id']
    data['rows'][3]['relevant'] = True
    with pytest.raises(ValueError, match='union_label'):
        validate_development(data)


def test_cli_private_outputs_nonoverwrite_and_no_profile(tmp_path, monkeypatch, capsys):
    from scripts import fit_relevance_scoring as cli
    from src.retrieval import semantic_evaluation
    monkeypatch.setattr(semantic_evaluation, 'REPOSITORY_ROOT', tmp_path)
    work = tmp_path / '.tmp'
    work.mkdir()
    source, target = work / 'development.json', work / 'candidate.json'
    data = development()
    for row in data['rows']:
        row['intent_group'] = 'family-' + str(int(row['case_id'].split('-')[1]) // 30)
    source.write_text(json.dumps(data))
    assert cli.main(['--dataset', str(source), '--output', str(target)]) == 0
    candidate = json.loads(target.read_text())
    assert 'holdout_passed' not in candidate
    assert 'threshold' not in candidate
    assert (work / 'candidate.training-report.json').is_file()
    previous = target.read_bytes()
    assert cli.main(['--dataset', str(source), '--output', str(target)]) == 2
    assert target.read_bytes() == previous
    assert cli.main(['--dataset', str(source), '--output', str(tmp_path / 'outside.json')]) == 2
    assert not (tmp_path / 'outside.json').exists()
    assert 'Synthetic reviewed fixture' not in capsys.readouterr().out
