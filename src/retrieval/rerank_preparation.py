"""Model-free baseline calibration workspace preparation, never promotion."""
from pathlib import Path
from uuid import UUID

from src.paths import REPOSITORY_ROOT
from src.catalog.inventory import reject_links
from src.ingestion import document_packages as packages
from .evaluation_cases import case_identity, validate_case_data, validate_cases, validate_online_case


class PreparationError(ValueError):
    def __init__(self, code, *, file=None, case_index=None):
        self.detail = {'code': code}
        if file is not None:
            self.detail['file'] = file
        if case_index is not None:
            self.detail['case_index'] = case_index
        super().__init__(code)


def path_at_root(value, *, private=False):
    path = Path(value)
    path = path if path.is_absolute() else REPOSITORY_ROOT / path
    reject_links(path)
    path = path.resolve()
    boundary = (REPOSITORY_ROOT / '.tmp').resolve() if private else REPOSITORY_ROOT.resolve()
    if not path.is_relative_to(boundary) or path == boundary:
        raise PreparationError('path_outside_workspace')
    return path


def relative(path):
    return path.relative_to(REPOSITORY_ROOT.resolve()).as_posix()


def read_json(path, snapshots):
    try:
        raw = path.read_bytes()
        data = packages.decode(raw)
    except OSError:
        raise PreparationError('input_unavailable', file=relative(path)) from None
    except ValueError:
        raise PreparationError('invalid_json', file=relative(path)) from None
    if not isinstance(data, dict):
        raise PreparationError('invalid_json_object', file=relative(path))
    if path in snapshots and snapshots[path] != raw:
        raise PreparationError('inputs_changed')
    snapshots[path] = raw
    return data


def load_workspace(manifest, *, stage='collection'):
    manifest = path_at_root(manifest, private=True)
    snapshots, items, entries = {}, [], []
    data = read_json(manifest, snapshots)
    datasets = data.get('datasets')
    if not isinstance(datasets, list) or not datasets:
        raise PreparationError('invalid_manifest', file=relative(manifest))
    seen_packages, seen_cases = set(), set()
    for dataset in datasets:
        if (not isinstance(dataset, dict) or not isinstance(dataset.get('package'), str)
                or not isinstance(dataset.get('cases'), str)):
            raise PreparationError('invalid_dataset', file=relative(manifest))
        package = path_at_root(dataset['package'])
        case_path = path_at_root(dataset['cases'], private=True)
        if not package.is_relative_to((REPOSITORY_ROOT / 'chunks').resolve()):
            raise PreparationError('package_outside_chunks')
        if package in seen_packages or case_path in seen_cases:
            raise PreparationError('duplicate_dataset', file=relative(manifest))
        seen_packages.add(package)
        seen_cases.add(case_path)
        try:
            record, payload, files = packages.load_package(REPOSITORY_ROOT, package, eligible=True)
        except (ValueError, OSError):
            raise PreparationError('package_invalid_or_ineligible', file=relative(package)) from None
        for name, content in files.items():
            snapshots[package / name] = content
        cases = read_json(case_path, snapshots)
        try:
            validated = validate_case_data(cases, record, payload)
        except (ValueError, TypeError, KeyError):
            # Re-run individual cases to locate errors without printing content.
            for index, case in enumerate(cases.get('cases', []) if isinstance(cases.get('cases'), list) else []):
                try:
                    validate_case_data({**cases, 'cases': [case]}, record, payload)
                except ValueError as exc:
                    raise PreparationError(str(exc), file=relative(case_path), case_index=index) from None
                except (TypeError, KeyError):
                    raise PreparationError('invalid_case', file=relative(case_path), case_index=index) from None
            raise PreparationError('invalid_cases', file=relative(case_path)) from None
        for index, case in enumerate(validated):
            try:
                validate_online_case(case)
                if type(case.get('top1_required', False)) is not bool:
                    raise ValueError('invalid_top1_required')
                if stage == 'calibration':
                    labels = case.get('candidate_labels')
                    if not isinstance(labels, dict) or any(type(v) is not bool for v in labels.values()):
                        raise ValueError('complete_candidate_labels_required')
                    for key in labels:
                        if str(UUID(key)) != key:
                            raise ValueError('invalid_candidate_id')
            except (ValueError, TypeError, AttributeError) as exc:
                code = str(exc) if str(exc) in {
                    'invalid_online_case', 'invalid_online_case_evidence', 'invalid_top1_required',
                    'complete_candidate_labels_required', 'invalid_candidate_id'} else 'invalid_candidate_id'
                raise PreparationError(code, file=relative(case_path), case_index=index) from None
            entries.append((case, record['revision_id'], None))
        items.append((package, case_path, cases, files))
    try:
        validate_cases(entries, stage == 'collection')
    except ValueError as exc:
        raise PreparationError(str(exc), file=relative(manifest)) from None
    return manifest, items, entries, snapshots


def binding(items):
    return [{'package': 'chunks/' + package.name, 'case_identity': case_identity(cases),
             'package_sha256': packages.files_hash(files)}
            for package, _, cases, files in items]


def verify_unchanged(snapshots):
    for path, raw in snapshots.items():
        if path.read_bytes() != raw:
            raise PreparationError('inputs_changed')


def write_workspace(output, items, snapshots, *, receipt, review):
    output = path_at_root(output, private=True)
    if output.exists() or any(path.is_relative_to(output) for path in snapshots):
        raise PreparationError('output_exists_or_overlaps_input')
    datasets, files = [], {}
    for index, (package, _, cases, _) in enumerate(items):
        name = f'cases-{index:03d}.json'
        datasets.append({'package': relative(package), 'cases': relative(output / name)})
        files[name] = packages.encoded(cases)
    files['manifest.json'] = packages.encoded({'datasets': datasets})
    receipt['input_hashes'] = {relative(p): packages.sha(raw) for p, raw in snapshots.items()}
    receipt['output_hashes'] = {name: packages.sha(raw) for name, raw in files.items()}
    files['receipt.json'] = packages.encoded(receipt)
    files['review.json'] = packages.encoded(review)
    verify_unchanged(snapshots)
    # Stage every output before the new directory becomes visible; never overwrite.
    packages.write_package(output.parent, output.name, files,
                           staging_parent=REPOSITORY_ROOT / '.tmp',
                           before_finalize=lambda: verify_unchanged(snapshots))
    return {'status': receipt['status'], 'manifest': relative(output / 'manifest.json'),
            'receipt': relative(output / 'receipt.json'), 'review': relative(output / 'review.json')}


def init_workspace(manifest, output, benchmark_arguments):
    source, items, entries, snapshots = load_workspace(manifest)
    target = path_at_root(output, private=True)
    command = ['python', '-m', 'scripts.benchmark_online_retrieval', '--manifest',
               relative(target / 'manifest.json'), *benchmark_arguments, '--collect-only',
               '--output', relative(target / 'collection-01.json')]
    return write_workspace(target, items, snapshots, receipt={
        'schema_version': 1, 'status': 'draft', 'source_manifest': relative(source),
        'case_count': len(entries), 'collection_arguments': command,
        'reviewed': False, 'binding': binding(items)}, review={
            'reviewed': False, 'review_notes': '',
            'source_evidence_checked': False, 'split_independence_checked': False,
            'inspected_families_in_regression': False, 'candidate_labels_checked': False})


def freeze_workspace(manifest, collection, review, output):
    manifest, items, entries, snapshots = load_workspace(manifest, stage='calibration')
    report = read_json(path_at_root(collection, private=True), snapshots)
    declaration = read_json(path_at_root(review, private=True), snapshots)
    required = ('reviewed', 'source_evidence_checked', 'split_independence_checked',
                'inspected_families_in_regression', 'candidate_labels_checked')
    if (any(declaration.get(k) is not True for k in required)
            or not isinstance(declaration.get('review_notes'), str) or not declaration['review_notes'].strip()):
        raise PreparationError('human_review_required')
    if (report.get('collect_only') is not True or report.get('rollout_ready') is not False
            or report.get('database_and_packages_unchanged') is not True
            or report.get('preparation_binding') != binding(items)
            or report.get('manifest_sha256') != packages.sha(snapshots[manifest])):
        raise PreparationError('collection_inputs_mismatch')
    rows = report.get('cases')
    cases = {case['id']: case for case, _, _ in entries}
    if (not isinstance(rows, list) or len(rows) != len(cases)
            or any(not isinstance(row, dict) for row in rows)
            or {row.get('id') for row in rows} != set(cases)):
        raise PreparationError('collection_case_mismatch')
    for row in rows:
        candidates = row.get('after_rerank')
        if not isinstance(candidates, list) or any(not isinstance(m, dict) for m in candidates):
            raise PreparationError('invalid_collection_candidates')
        ids = [m.get('chunk_id') for m in candidates]
        if (any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids)
                or set(ids) != set(cases[row['id']]['candidate_labels'])):
            raise PreparationError('complete_candidate_labels_required')
    arguments = report.get('reproduction_arguments')
    validate_benchmark_arguments(arguments)
    target = path_at_root(output, private=True)
    command = ['python', '-m', 'scripts.benchmark_online_retrieval', '--manifest',
               relative(target / 'manifest.json'), *arguments, '--output', relative(target / 'evaluation-01.json')]
    return write_workspace(target, items, snapshots, receipt={
        'schema_version': 1, 'status': 'frozen', 'case_count': len(entries),
        'benchmark_arguments': command, 'binding': binding(items),
        'collection_sha256': packages.sha(snapshots[path_at_root(collection, private=True)]),
        'collection_identity': {key: report.get(key) for key in (
            'fingerprint', 'corpus_fingerprint', 'model_files_sha256', 'reproduction_arguments')},
        'review_is_operator_declaration': True, 'profile_accepted': False}, review=declaration)


def validate_benchmark_arguments(arguments):
    """Accept only the fixed baseline invocation produced by this module."""
    flags = ['--model-path', '--model-revision', '--rerank-model-path', '--rerank-model-file',
             '--rerank-candidates', '--rerank-batch-size', '--requests']
    if (not isinstance(arguments, list) or len(arguments) != 15 or arguments[-1] != '--rerank'
            or arguments[:-1:2] != flags or any(not isinstance(v, str) or not v or v.startswith('--')
                                               for v in arguments[1:-1:2])):
        raise PreparationError('collection_reproduction_missing')
    from .reranking import RerankSettings
    import re
    try:
        if not re.fullmatch('[a-fA-F0-9]{40}', arguments[3]) or int(arguments[13]) < 100:
            raise ValueError()
        RerankSettings(enabled=True, model_path=Path(arguments[5]), model_file=arguments[7],
                       candidate_count=int(arguments[9]), batch_size=int(arguments[11]))
    except ValueError:
        raise PreparationError('invalid_benchmark_configuration') from None
