"""Bounded, hash-bound development archives; no models, credentials or DB IO."""
import hashlib
import json
import math
import platform
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

from .scoring import FEATURES, VERSION, apply_scoring, validate_scoring
from .scoring_training import fit_development, validate_development

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 64 * 1024 * 1024
CONTRACT = 'rita-relevance-development-v1'
INPUTS = {'development.json', 'manifest.json'}
OUTPUTS = {'candidate.json', 'training-report.json', 'compatibility-report.json', 'manifest.json'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('nonfinite_json')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def kit_identity():
    if sys.version_info < (3, 12):
        raise ValueError('kit_requires_python_312')
    manifest = decode((ROOT / 'kit-manifest.json').read_bytes())
    if manifest['contract'] != CONTRACT or manifest['feature_version'] != VERSION:
        raise ValueError('kit_contract_mismatch')
    for name, expected in manifest['files_sha256'].items():
        logical = PurePosixPath(name)
        if logical.is_absolute() or '..' in logical.parts or '\\' in name or ':' in name:
            raise ValueError('unsafe_kit_manifest')
        path = ROOT / name
        if path.is_symlink() or sha(path.read_bytes()) != expected:
            raise ValueError('kit_file_changed')
    return sha(encode(manifest))


def read_archive(path, expected):
    """Never extract supplied archives; reject extra, duplicate and linked entries."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) != len(expected) or {e.filename for e in entries} != expected:
            raise ValueError('unexpected_archive_members')
        total = 0
        result = {}
        for item in entries:
            mode = item.external_attr >> 16
            if (item.is_dir() or stat.S_ISLNK(mode)
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or item.flag_bits & 1):
                raise ValueError('unsafe_archive_member')
            total += item.file_size
            if total > LIMIT:
                raise ValueError('archive_too_large')
            with archive.open(item) as stream:
                raw = stream.read(LIMIT + 1)
            if len(raw) != item.file_size or len(raw) > LIMIT:
                raise ValueError('archive_too_large')
            result[item.filename] = raw
    return result


def write_archive(path, files):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation; never overwrite previous experiments.
    with path.open('xb') as stream:
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, raw in sorted(files.items()):
                archive.writestr(name, raw)


def bound_files(files, kind, members):
    manifest = decode(files['manifest.json'])
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or manifest.get('contract') != CONTRACT
            or manifest.get('kind') != kind or manifest.get('kit_identity') != kit_identity()
            or manifest.get('data_kind') not in ('reviewed', 'synthetic')
            or manifest.get('files_sha256') != {n: sha(files[n]) for n in members}):
        raise ValueError('bundle_binding_mismatch')
    return manifest


def load_input(path):
    files = read_archive(path, INPUTS)
    manifest = bound_files(files, 'development_input', INPUTS - {'manifest.json'})
    data = decode(files['development.json'])
    if set(data) != {'schema_version', 'split', 'reviewed', 'review_notes', 'version', 'feature_names', 'rows'}:
        raise ValueError('unexpected_development_fields')
    rows = validate_development(data)
    keys = {'case_id', 'intent_group', 'arm', 'chunk_id', 'features', 'relevant', 'required_groups'}
    if any(set(row) != keys for row in data['rows']):
        raise ValueError('unexpected_development_fields')
    if {r['arm'] for r in rows} != {'baseline', 'semantic'}:
        raise ValueError('baseline_semantic_arms_required')
    return files, manifest, data, rows


def export_input(dataset, output, *, synthetic=False):
    """Strip free text and unknown fields before upload; retain identity strings."""
    raw = Path(dataset).read_bytes()
    data = decode(raw)
    rows = validate_development(data)
    provenance = {}
    if not synthetic:
        for key in ('dataset_sha256', 'collection_sha256', 'review_sha256', 'corpus_fingerprint'):
            value = data.get(key)
            if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
                raise ValueError('review_bridge_provenance_required')
            provenance[key] = value
        packages = data.get('source_package_hashes')
        if (not isinstance(packages, dict) or not packages
                or any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v)
                       for v in packages.values())):
            raise ValueError('review_bridge_provenance_required')
        provenance['source_package_map_sha256'] = sha(encode(packages))
    if {r['arm'] for r in rows} != {'baseline', 'semantic'}:
        raise ValueError('baseline_semantic_arms_required')
    cleaned = {'schema_version': 1, 'split': 'development', 'reviewed': True,
        'review_notes': 'Synthetic demonstration only.' if synthetic else 'Review retained locally; see bound hashes.',
        'version': VERSION, 'feature_names': list(FEATURES), 'rows': rows}
    contents = {'development.json': encode(cleaned)}
    manifest = {'schema_version': 1, 'contract': CONTRACT, 'kind': 'development_input',
        'kit_identity': kit_identity(), 'data_kind': 'synthetic' if synthetic else 'reviewed',
        'source_file_sha256': sha(raw), 'provenance': provenance,
        'files_sha256': {n: sha(v) for n, v in contents.items()}}
    if Path(dataset).read_bytes() != raw:
        raise ValueError('development_input_changed')
    write_archive(output, contents | {'manifest.json': encode(manifest)})
    return {'status': 'development_input_exported', 'accepted_profile': False}


def cpu_check(rows, scoring):
    """Independent numeric expression cross-check, not a quality benchmark."""
    import numpy as np
    features = [r['features'] for r in rows]
    reference = apply_scoring(features, scoring)
    actual = (((np.asarray(features, dtype=np.float64) - scoring['means'])
               / scoring['scales']) * scoring['weights']).sum(axis=1) + scoring['bias']
    if not np.allclose(reference, actual, rtol=1e-9, atol=1e-10):
        raise ValueError('cpu_scoring_parity_failed')
    return {'status': 'passed', 'numpy_version': np.__version__, 'python_version': platform.python_version(),
            'rows': len(rows),
            'max_absolute_error': max(abs(a - b) for a, b in zip(reference, actual.tolist())),
            'rtol': 1e-9, 'atol': 1e-10, 'acceptance_evidence': False}


def fit_bundle(bundle, output, *, gpu=False):
    files, manifest, data, rows = load_input(bundle)
    scoring, report = fit_development(data)
    report['input_file_sha256'] = sha(files['development.json'])
    compatibility = {'schema_version': 1, 'cpu': cpu_check(rows, scoring),
        'gpu': {'status': 'skipped', 'reason': 'not_requested', 'acceptance_evidence': False}}
    if gpu:
        from .gpu import diagnose
        compatibility['gpu'] = diagnose(rows, scoring)
    contents = {'candidate.json': encode(scoring), 'training-report.json': encode(report),
                'compatibility-report.json': encode(compatibility)}
    result = {'schema_version': 1, 'contract': CONTRACT, 'kind': 'development_candidate',
        'kit_identity': manifest['kit_identity'], 'data_kind': manifest['data_kind'],
        'input_manifest_sha256': sha(files['manifest.json']), 'accepted_profile': False,
        'files_sha256': {n: sha(v) for n, v in contents.items()}}
    # Refuse input mutation during a long fit.
    if read_archive(bundle, INPUTS) != files:
        raise ValueError('development_input_changed')
    write_archive(output, contents | {'manifest.json': encode(result)})
    return {'status': 'development_candidate_only', 'accepted_profile': False,
            'cases': report['cases'], 'gpu': compatibility['gpu']['status']}


def equivalent(left, right):
    """Re-fit comparison permits only FP64 numerical differences, not metadata drift."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (float, int)) and isinstance(right, (float, int)):
        return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-10)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(equivalent(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(equivalent(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def import_result(bundle, result, output):
    """Re-fit on local CPU; store only candidates, never runtime artifacts."""
    inputs, original, data, rows = load_input(bundle)
    files = read_archive(result, OUTPUTS)
    manifest = bound_files(files, 'development_candidate', OUTPUTS - {'manifest.json'})
    if (manifest.get('accepted_profile') is not False or manifest['data_kind'] != original['data_kind']
            or manifest.get('input_manifest_sha256') != sha(inputs['manifest.json'])):
        raise ValueError('candidate_input_mismatch')
    scoring = validate_scoring(decode(files['candidate.json']))
    report = decode(files['training-report.json'])
    expected, expected_report = fit_development(data)
    expected_report['input_file_sha256'] = sha(inputs['development.json'])
    # Numerical coefficients can differ at FP64 precision across BLAS builds.
    expected_report['scoring_digest'] = scoring['digest']
    if (not equivalent({k: v for k, v in scoring.items() if k != 'digest'},
                       {k: v for k, v in expected.items() if k != 'digest'})
            or not equivalent(report, expected_report)):
        raise ValueError('local_refit_mismatch')
    receipt = {'status': 'synthetic_candidate_only' if original['data_kind'] == 'synthetic'
               else 'development_candidate_only', 'accepted_profile': False,
               'input_manifest_sha256': sha(inputs['manifest.json']),
               'result_manifest_sha256': sha(files['manifest.json']),
               'local_cpu': cpu_check(rows, scoring)}
    if read_archive(bundle, INPUTS) != inputs or read_archive(result, OUTPUTS) != files:
        raise ValueError('import_inputs_changed')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        with (output / name).open('xb') as stream:
            stream.write(raw)
    with (output / 'import-receipt.json').open('xb') as stream:
        stream.write(encode(receipt))
    return receipt


def example():
    """300 fictional cases; demonstrates mechanics, never business acceptance."""
    rows = []
    for case in range(300):
        supported = case % 2 == 0
        groups = [[f'positive-{case}']] if supported else []
        for arm in ('baseline', 'semantic'):
            for index in range(2):
                relevant = supported and index == 0
                rows.append({'case_id': f'case-{case}', 'intent_group': f'family-{case // 30}',
                    'arm': arm, 'chunk_id': f'positive-{case}' if relevant else f'negative-{case}-{index}',
                    'features': [3. if relevant else -3., 0., 0., 1., .8 if relevant else .1, 1., 1., 1.],
                    'relevant': relevant, 'required_groups': groups})
    return {'schema_version': 1, 'split': 'development', 'reviewed': True,
            'review_notes': 'Fictional labels for software verification only.',
            'feature_names': list(FEATURES), 'rows': rows}
