"""Gated, byte-preserving semantic profile promotion; no model or database IO.

The caller supplies the runtime inventory verifier. Report/review declarations
are checked for consistency, not authenticated as proof of human identity or of
model execution. Promotion never converts a failed evaluation into acceptance.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

from .scoring import validate_scoring
from .semantic_evaluation import compare, summarize_rows, validate_dataset
from .semantics import validate_artifact

INVENTORY = 'artifacts/runtime-assets.json'
GATES = {'calibration_available', 'complete_evidence', 'no_irrelevant_returned',
         'no_unsupported_acceptances', 'critical', 'required_top1', 'latency'}
COMPARISONS = {'holdout_macro_ndcg5_improved', 'complete_evidence_not_reduced',
               'no_domain_evidence_regression', 'no_holdout_domain_evidence_regression', 'promotion_ready'}
HEX = re.compile(r'[0-9a-f]{64}')


class PromotionError(ValueError):
    """Fixed public error code; partial writes require explicit reconciliation."""


def _fail(code):
    raise PromotionError(code)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail('duplicate_input_json_key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
            parse_constant=lambda _: _fail('invalid_input_json'))
    except (UnicodeError, json.JSONDecodeError):
        _fail('invalid_input_json')


def _checked(root, value, *, private=False):
    """Reject link/reparse components before resolving or reading a path."""
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    try:
        relative = path.relative_to(root)
    except ValueError:
        _fail('promotion_path_outside_repository')
    if (not relative.parts or any(part in ('', '.', '..') for part in relative.parts)
            or private and relative.parts[0] != '.tmp'):
        _fail('promotion_path_not_allowed')
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            _fail('promotion_linked_path')
    return path


def _read(path):
    raw = path.read_bytes()
    return raw, _json(raw)


def _hex(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _true_fields(value, expected, code):
    if not isinstance(value, dict) or set(value) != expected or any(v is not True for v in value.values()):
        _fail(code)


def _validate_arm(arm, cases):
    """Recompute summary fields consumed by gates/comparison from report rows."""
    if not isinstance(arm, dict) or not _hex(arm.get('fingerprint')):
        _fail('invalid_pipeline_report')
    rows, summary = arm.get('cases'), arm.get('summary')
    if not isinstance(rows, list) or not isinstance(summary, dict) or len(rows) != len(cases):
        _fail('evaluation_case_binding_mismatch')
    indexed = {case['id']: case for case in cases}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get('id') not in indexed or row['id'] in seen:
            _fail('evaluation_case_binding_mismatch')
        seen.add(row['id'])
        source = indexed[row['id']]
        if any(row.get(key) != source[key] for key in
               ('domain', 'intent_group', 'split', 'relevant', 'critical', 'top1_required')):
            _fail('evaluation_case_binding_mismatch')
        if any(type(row.get(key)) is not bool for key in ('relevant', 'critical', 'top1_required')):
            _fail('invalid_evaluation_metrics')
        for key in ('retained5', 'irrelevant20', 'duplicate5'):
            if type(row.get(key)) is not int or row[key] < 0:
                _fail('invalid_evaluation_metrics')
        if row['retained5'] > 5 or row['irrelevant20'] > 20:
            _fail('invalid_evaluation_metrics')
        for key in ('top1_relevant', 'required_top1_passed', 'false_empty', 'unfiltered_top1_relevant'):
            if type(row.get(key)) is not bool:
                _fail('invalid_evaluation_metrics')
        for key in ('candidate_group_recall30', 'candidate_complete30', 'complete5',
                    'unfiltered_complete5', 'ndcg5'):
            value = row.get(key)
            if value is not None and (type(value) not in (bool, int, float)
                    or not math.isfinite(value) or not 0 <= value <= 1):
                _fail('invalid_evaluation_metrics')
        if row['relevant'] and type(row.get('complete5')) is not bool:
            _fail('invalid_evaluation_metrics')
        ranks = row.get('group_ranks')
        if (not isinstance(ranks, list) or len(ranks) != len(source['required_groups'])
                or any(rank is not None and (type(rank) is not int or not 1 <= rank <= 5) for rank in ranks)
                or row['false_empty'] is not (row['relevant'] and row['retained5'] == 0)
                or row['required_top1_passed'] is not (1 in ranks)
                or row['relevant'] and row['complete5'] is not all(rank is not None for rank in ranks)
                or row['relevant'] and row['complete5'] and row['retained5'] == 0):
            _fail('invalid_evaluation_metrics')
    domains = sorted({case['domain'] for case in cases})
    computed = summarize_rows(rows)
    computed['domains'] = {domain: summarize_rows([r for r in rows if r['domain'] == domain])
                           for domain in domains}
    computed['holdout_domains'] = {domain: summarize_rows([r for r in rows
        if r['domain'] == domain and r['split'] == 'holdout']) for domain in domains}
    computed['splits'] = {split: summarize_rows([r for r in rows if r['split'] == split])
                          for split in ('calibration', 'holdout', 'regression')}
    holdout_ndcg = [d['ndcg5'] for d in computed['holdout_domains'].values() if d['ndcg5'] is not None]
    computed['holdout_macro_ndcg5'] = sum(holdout_ndcg) / len(holdout_ndcg) if holdout_ndcg else None
    if any(summary.get(key) != value for key, value in computed.items()):
        _fail('evaluation_summary_mismatch')
    return rows, summary


def _validate_inputs(report, dataset, review, profile, semantic, training, inventory, raws):
    if (type(report.get('schema_version')) is not int or report['schema_version'] != 1
            or report.get('collect_only') is not False or report.get('rollout_ready') is not True
            or report.get('database_and_packages_unchanged') is not True):
        _fail('full_passing_evaluation_required')
    cases = validate_dataset(dataset)
    dataset_hash = _sha(raws['dataset'])
    if report.get('dataset_sha256') != dataset_hash:
        _fail('evaluation_dataset_mismatch')
    if (review.get('dataset_sha256') != dataset_hash
            or any(review.get(key) is not True for key in ('human_approved', 'source_evidence_checked',
                'candidate_labels_checked', 'split_independence_checked'))
            or not isinstance(review.get('review_statement'), str) or not review['review_statement'].strip()):
        _fail('bound_human_review_required')
    pipelines = report.get('pipelines')
    if not isinstance(pipelines, dict) or set(pipelines) != {'baseline', 'semantic'}:
        _fail('semantic_only_paired_report_required')
    baseline, candidate = pipelines['baseline'], pipelines['semantic']
    _validate_arm(baseline, cases)
    rows, summary = _validate_arm(candidate, cases)
    _true_fields(candidate.get('gates'), GATES, 'semantic_quality_gates_failed')
    if candidate.get('absolute_gates_passed') is not True:
        _fail('semantic_quality_gates_failed')
    comparisons = report.get('comparisons')
    if not isinstance(comparisons, dict) or set(comparisons) != {'semantic_vs_baseline'}:
        _fail('semantic_comparison_failed')
    _true_fields(comparisons['semantic_vs_baseline'], COMPARISONS, 'semantic_comparison_failed')
    if compare(baseline, candidate) != comparisons['semantic_vs_baseline']:
        _fail('semantic_comparison_failed')
    if (any(summary['splits'][s]['complete5'] is None or summary['splits'][s]['complete5'] < .9
            for s in ('calibration', 'holdout'))
            or any(r['irrelevant20'] != 0 or not r['relevant'] and r['retained5'] != 0 for r in rows)
            or any(r['critical'] and (r['complete5'] is not True if r['relevant'] else r['retained5'] != 0)
                   or r['top1_required'] and r['required_top1_passed'] is not True for r in rows)
            or type(summary.get('latency_requests')) is not int or summary['latency_requests'] < 100
            or not _finite(summary.get('p95_seconds')) or not 0 <= summary['p95_seconds'] <= 5):
        _fail('semantic_quality_gates_failed')
    threshold = candidate.get('threshold')
    if not _finite(threshold) or not all(_hex(report.get(k)) for k in ('corpus_fingerprint', 'publication_snapshot')):
        _fail('invalid_evaluation_identity')
    scoring = validate_scoring(report.get('scoring'))
    expected = {'schema_version': 3, 'fingerprint': candidate['fingerprint'],
        'corpus_fingerprint': report['corpus_fingerprint'], 'threshold': threshold,
        'holdout_passed': True, 'scoring': scoring}
    if (profile != expected or type(profile.get('schema_version')) is not int
            or profile.get('holdout_passed') is not True or not _finite(profile.get('threshold'))):
        _fail('emitted_profile_mismatch')
    validate_artifact(semantic)
    if (training.get('training_only') is not True or training.get('accepted_profile') is not False
            or training.get('holdout_evaluated') is not False
            or training.get('scoring_digest') != scoring['digest']
            or not _hex(training.get('dataset_sha256'))):
        _fail('scoring_training_mismatch')
    groups, ids = training.get('development_intent_groups'), training.get('development_case_ids')
    if (not isinstance(groups, list) or not groups or not isinstance(ids, list) or len(ids) < 300
            or any(not isinstance(v, str) or not v for v in groups + ids)
            or len(groups) != len(set(groups)) or len(ids) != len(set(ids))):
        _fail('scoring_training_mismatch')
    if any(case['split'] in ('calibration', 'holdout') and
            (case['intent_group'] in groups or case['id'] in ids) for case in cases):
        _fail('development_acceptance_leak')
    config = report.get('release_configuration')
    expected_config = {'semantic_mode', 'intent_mode', 'candidate_count', 'batch_size', 'model_revision',
        'rerank_model_revision', 'semantic_artifact_sha256', 'scoring_digest', 'training_report_sha256'}
    if (not isinstance(config, dict) or set(config) != expected_config
            or config['semantic_mode'] != 'expand' or config['intent_mode'] != 'off'
            or type(config['candidate_count']) is not int or config['candidate_count'] != 30
            or type(config['batch_size']) is not int or not 1 <= config['batch_size'] <= 16
            or config['scoring_digest'] != scoring['digest']
            or config['semantic_artifact_sha256'] != _sha(raws['semantic'])
            or config['training_report_sha256'] != _sha(raws['training'])
            or config['model_revision'] != inventory['models']['e5']['revision']
            or config['rerank_model_revision'] != inventory['models']['reranker']['revision']
            or report.get('model_files_sha256') != inventory['models']['e5']['files_sha256']):
        _fail('release_configuration_mismatch')
    bindings = report.get('artifacts')
    if (not isinstance(bindings, dict) or set(bindings) != {'0', '1', '2'}
            or not all(_hex(v) for v in bindings.values())
            or bindings['0'] != _sha(raws['semantic']) or bindings['2'] != _sha(raws['training'])):
        _fail('evaluation_artifact_mismatch')
    return cases, config, scoring


def promote_profile(*, report, dataset, review, profile, semantic_artifact, training_report,
                    version, root, verify_inventory, expected_current_sha256=None):
    """Validate and replace the current pair; replace inventory last.

    The verifier callback receives ``(root=Path, manifest=relative_string)`` and
    returns ``(content_safe_result, exit_code)``. A surviving lock or partial
    replacement after failure is a reconciliation boundary, never an auto-retry.
    """
    root = Path(root).absolute()
    if (not isinstance(version, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,119}', version) is None
            or version.endswith('.')):
        _fail('invalid_rerank_version')
    paths = {name: _checked(root, value, private=True) for name, value in
        [('report', report), ('dataset', dataset), ('review', review), ('profile', profile), ('training', training_report)]}
    paths['semantic'] = _checked(root, semantic_artifact)
    if paths['profile'] != paths['report'].with_name(paths['report'].stem + '.semantic.profile.json'):
        _fail('expected_emitted_profile_required')
    if len(set(paths.values())) != len(paths):
        _fail('promotion_input_alias')
    inventory_path = _checked(root, INVENTORY)
    target_relative = 'artifacts/rerank'
    target = _checked(root, target_relative)
    lock = _checked(root, '.tmp/rerank-promotion.lock')
    if lock.exists():
        _fail('promotion_locked_reconcile_before_retry')
    names = ('relevance-profile.json', 'manifest.json')
    if target.exists() and (not target.is_dir() or {p.name for p in target.iterdir()} != set(names)):
        _fail('current_rerank_pair_incomplete')
    current = {name: _checked(root, target_relative + '/' + name).read_bytes()
               for name in names} if target.exists() else {}
    if current:
        if expected_current_sha256 is None:
            _fail('expected_current_sha256_required')
        if not _hex(expected_current_sha256) or _sha(current[names[0]]) != expected_current_sha256:
            _fail('current_rerank_sha256_mismatch')
    elif expected_current_sha256 is not None:
        _fail('current_rerank_profile_missing')
    raw_inventory, inventory = _read(inventory_path)
    preflight, code = verify_inventory(root=root, manifest=INVENTORY)
    if code != 0 or preflight.get('errors'):
        _fail('runtime_inventory_preflight_failed')
    existing_entries = [e for e in inventory['assets'] if e['group'] == 'rerank']
    if (len(existing_entries) != (2 if current else 0)
            or {e['path'] for e in existing_entries} !=
            ({target_relative + '/' + name for name in names} if current else set())):
        _fail('current_rerank_inventory_mismatch')
    raws, parsed = {}, {}
    for name, path in paths.items():
        raws[name], parsed[name] = _read(path)
    if any(not isinstance(value, dict) for value in parsed.values()):
        _fail('invalid_promotion_input_shape')
    semantic_relative = paths['semantic'].relative_to(root).as_posix()
    lexicon_entries = [e for e in inventory['assets'] if e['path'] == semantic_relative
        and e['group'] == 'semantics' and e['sha256'] == _sha(raws['semantic'])
        and e['size_bytes'] == len(raws['semantic'])]
    if len(lexicon_entries) != 1:
        _fail('semantic_artifact_not_in_inventory')
    cases, config, scoring = _validate_inputs(parsed['report'], parsed['dataset'], parsed['review'],
        parsed['profile'], parsed['semantic'], parsed['training'], inventory, raws)
    artifact_relative = target_relative + '/relevance-profile.json'
    manifest_relative = target_relative + '/manifest.json'
    provenance = {'schema_version': 1, 'status': 'retrieval_evaluation_accepted',
        'artifact': {'path': artifact_relative, 'sha256': _sha(raws['profile']), 'size_bytes': len(raws['profile'])},
        'runtime_release_accepted': True, 'release_version': version,
        'evaluation': {'report_sha256': _sha(raws['report']), 'dataset_sha256': _sha(raws['dataset']),
            'human_review_sha256': _sha(raws['review']), 'training_report_sha256': _sha(raws['training']),
            'cases': len(cases), 'pipeline_fingerprint': parsed['profile']['fingerprint'],
            'corpus_fingerprint': parsed['profile']['corpus_fingerprint'],
            'publication_snapshot': parsed['report']['publication_snapshot']},
        'models': inventory['models'], 'semantic_artifact': {'path': semantic_relative,
            'sha256': _sha(raws['semantic']), 'package_fingerprint': parsed['semantic']['package_fingerprint']},
        'scoring_digest': scoring['digest'], 'release_configuration': config,
        'api_deployed': False, 'environment_configuration_changed': False}
    manifest_bytes = _bytes(provenance)
    updated = _json(raw_inventory)
    updated['assets'] = [e for e in updated['assets'] if e['group'] != 'rerank']
    for relative, raw in ((artifact_relative, raws['profile']), (manifest_relative, manifest_bytes)):
        updated['assets'].append({'path': relative, 'size_bytes': len(raw), 'sha256': _sha(raw), 'group': 'rerank'})
    updated_bytes = _bytes(updated)
    temporary = _checked(root, '.tmp')
    temporary.mkdir(exist_ok=True)
    try:
        lock_stream = lock.open('xb')
    except FileExistsError:
        _fail('promotion_locked_reconcile_before_retry')
    stage = _checked(root, '.tmp/rerank-promotion-' + uuid.uuid4().hex)
    lock_bytes = ('rerank-promotion:' + stage.name + '\n').encode('utf-8')
    wrote_current = False
    complete = False
    try:
        with lock_stream:
            lock_stream.write(lock_bytes)
        stage.mkdir()
        stage_profile, stage_manifest, stage_inventory = (stage / 'relevance-profile.json',
            stage / 'manifest.json', stage / 'runtime-assets.json')
        for path, raw in ((stage_profile, raws['profile']), (stage_manifest, manifest_bytes), (stage_inventory, updated_bytes)):
            with path.open('xb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        # Preserve exact recovery bytes outside the running artifact tree.
        previous = stage / 'previous'
        previous.mkdir()
        for name, raw in {'runtime-assets.json': raw_inventory, **current}.items():
            with (previous / name).open('xb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())

        def unchanged():
            if (not lock.exists() or lock.read_bytes() != lock_bytes
                    or inventory_path.read_bytes() != raw_inventory
                    or any(path.read_bytes() != raws[key] for key, path in paths.items())):
                _fail('promotion_inputs_changed')
            _checked(root, target_relative)
            if target.exists():
                if not target.is_dir() or {p.name for p in target.iterdir()} != set(current):
                    _fail('promotion_current_changed')
            elif current:
                _fail('promotion_current_changed')
            for name, raw in current.items():
                if _checked(root, target_relative + '/' + name).read_bytes() != raw:
                    _fail('promotion_current_changed')

        unchanged()
        target.parent.mkdir(parents=True, exist_ok=True)
        if not current:
            target.mkdir(exist_ok=False)
        wrote_current = True
        # Each rename is atomic, but the pair and inventory are not one transaction.
        for name, staged, raw in ((names[0], stage_profile, raws['profile']),
                                  (names[1], stage_manifest, manifest_bytes)):
            unchanged()
            os.replace(staged, _checked(root, target_relative + '/' + name))
            current[name] = raw
        candidate_result, code = verify_inventory(root=root, manifest=stage_inventory.relative_to(root).as_posix())
        if code != 0 or candidate_result.get('errors'):
            _fail('promoted_inventory_candidate_failed')
        unchanged()
        # Atomic inventory replacement follows a last-byte concurrency check.
        # Writers bypassing the cooperative lock cannot be globally serialized;
        # any detected conflict is retained for explicit reconciliation.
        _checked(root, INVENTORY)
        os.replace(stage_inventory, inventory_path)
        final_result, code = verify_inventory(root=root, manifest=INVENTORY)
        if (code != 0 or final_result.get('errors')
                or (target / 'relevance-profile.json').read_bytes() != raws['profile']
                or (target / 'manifest.json').read_bytes() != manifest_bytes
                or inventory_path.read_bytes() != updated_bytes
                or any(path.read_bytes() != raws[name] for name, path in paths.items())):
            _fail('promotion_postflight_failed')
        if not lock.exists() or lock.read_bytes() != lock_bytes:
            _fail('promotion_lock_changed')
        lock.unlink()
        complete = True
        return {'status': 'promoted', 'profile': artifact_relative, 'manifest': manifest_relative,
            'profile_sha256': _sha(raws['profile']), 'manifest_sha256': _sha(manifest_bytes),
            'inventory_sha256': _sha(updated_bytes), 'inventory_files': final_result.get('files'),
            'api_deployed': False, 'environment_configuration_changed': False}
    except Exception as exc:
        if wrote_current:
            receipt = {'status': 'partial_promotion_reconcile_required', 'version': version,
                'code': str(exc) if isinstance(exc, PromotionError) else 'promotion_write_failed',
                'inventory_before_sha256': _sha(raw_inventory), 'inventory_expected_sha256': _sha(updated_bytes)}
            try:
                (stage / 'reconciliation.json').write_bytes(_bytes(receipt))
            except OSError:
                pass
            _fail('partial_promotion_reconcile_required')
        if isinstance(exc, PromotionError):
            raise
        _fail('promotion_write_failed')
    finally:
        if not complete and not wrote_current:
            if lock.exists() and lock.read_bytes() == lock_bytes:
                lock.unlink()
