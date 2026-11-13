"""Read-only, package-pinned online evaluation and CPU latency gate.

Real question manifests and metadata-only reports must stay under .tmp.
Each dataset supplies package/cases paths using evaluate_retrieval's case schema.
Cases additionally require split (calibration/holdout/regression), relevant (bool),
and optionally top1_required (bool). Unsupported questions must be source-reviewed.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import time

from scripts.evaluate_retrieval import database_state, load_cases, grade, summarize, output_path
from src.ingestion import document_packages as packages
from src.ingestion.approved_publication import configuration
from src.ingestion.embeddings import model_fingerprint
from src.paths import REPOSITORY_ROOT
from src.retrieval import RetrievalService
from src.retrieval.config import add_arguments, from_arguments
from src.database import close_database
from src.database.runtime import run_async


def calibrate(rows):
    calibration = [r for r in rows if r['split'] == 'calibration']
    holdout = [r for r in rows if r['split'] == 'holdout']
    if any(not any(r['relevant'] is label for r in group) for group in (calibration, holdout) for label in (False, True)):
        return {'threshold': None, 'holdout_passed': False}
    negatives = [m['rerank_score'] for r in calibration for m in r['after_rerank'] if not m['relevant']]
    if not negatives:
        return {'threshold': None, 'holdout_passed': False}
    threshold = math.nextafter(max(negatives), math.inf)
    if not math.isfinite(threshold):
        return {'threshold': None, 'holdout_passed': False}
    for row in rows:
        retained = [m for m in row['after_rerank'] if m['rerank_score'] >= threshold]
        row['filtered_by_top_k'] = {}
        # Certify the entire exposed range, not only the top-five benchmark.
        for top_k in range(1, 21):
            selected = retained[:top_k]
            row['filtered_by_top_k'][str(top_k)] = {
                'retained': len(selected), 'irrelevant': sum(not m['relevant'] for m in selected)}
        selected = retained[:5]
        row.update(grade(row['_case'], selected, row['_revision'], row['_run']))
        row['retained_count'] = len(selected)
        row['removed_count'] = len(row['after_rerank']) - len(retained)
        row['top1_relevant'] = bool(selected) and selected[0]['relevant']
        row['required_top1_passed'] = any(rank == 1 for rank in row['group_ranks'])
        row['false_empty'] = row['relevant'] and not selected
    def metrics(group):
        positives = [r for r in group if r['relevant']]
        return {
            'positive_complete_recall': sum(r['evidence_complete'] is True for r in positives) / len(positives),
            'no_irrelevant_returned': all(r['filtered_by_top_k']['20']['irrelevant'] == 0 for r in group),
            'no_unsupported_acceptances': all(r['retained_count'] == 0 for r in group if not r['relevant']),
            'false_empty_count': sum(r['false_empty'] for r in positives),
        }
    measured = {name: metrics(group) for name, group in [('calibration', calibration), ('holdout', holdout)]}
    passed = all(m['positive_complete_recall'] >= .9 and m['no_irrelevant_returned']
                 and m['no_unsupported_acceptances'] for m in measured.values())
    return {'threshold': threshold, 'holdout_passed': passed, **measured}


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


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def validate_outputs(args):
    args.output = output_path(args.output)
    output_path(args.manifest)
    manifest = json.loads(args.manifest.read_bytes())
    protected_files = {args.manifest.resolve()}
    protected_directories = {args.model_path.resolve(), from_arguments(args).model_path.resolve()}
    for dataset in manifest['datasets']:
        protected_files.add(Path(dataset['cases']).resolve())
        protected_directories.add(Path(dataset['package']).resolve())
    for path in (args.output, args.output.with_suffix('.profile.json')):
        if (path.exists() or path in protected_files
                or any(path.is_relative_to(directory) for directory in protected_directories)):
            raise ValueError('output_overwrites_input')
    if args.requests < 100:
        raise ValueError('insufficient_latency_requests')


async def benchmark(args):
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    entries, snapshots = [], []
    fingerprint = model_fingerprint(args.model_path)
    for dataset in manifest['datasets']:
        package, cases_path = Path(dataset['package']), Path(dataset['cases'])
        record, payload, files = packages.load_package(REPOSITORY_ROOT, package, eligible=True)
        cases = load_cases(cases_path, record, payload)
        expected = configuration(payload, args.model_revision)
        expected.update(package_sha256=packages.files_hash(files), model_files_sha256=fingerprint,
                        metadata_sha256=packages.sha(files['metadata.json']))
        before = await database_state(record, payload, expected)
        snapshots.append((package, cases_path, cases_path.read_bytes(), record, payload, files, expected, before))
        for case in cases:
            if case.get('split') not in ('calibration', 'holdout', 'regression') or type(case.get('relevant')) is not bool:
                raise ValueError('invalid_online_case')
            if case['relevant'] != bool(case['required_groups']):
                raise ValueError('invalid_online_case_evidence')
            entries.append((case, record['revision_id'], before['run_id']))
    validate_cases(entries, args.collect_only)
    settings = from_arguments(args)
    if not settings.enabled or settings.profile_path is not None:
        raise ValueError('benchmark_requires_uncalibrated_reranker')
    service = RetrievalService(model_path=args.model_path, model_revision=args.model_revision, device='cpu', rerank=settings)
    try:
        await service.start()
        await service._retrieve(entries[0][0]['question'], 5, assessment=False)
        rows = []
        corpus_fingerprint = None
        for case, revision, run in entries:
            started = time.perf_counter()
            result = await service._retrieve(case['question'], 5, assessment=False)
            if result['status'] == 'model_mismatch':
                raise ValueError('model_mismatch')
            current_corpus = result.get('corpus_fingerprint')
            if not current_corpus or (corpus_fingerprint and current_corpus != corpus_fingerprint):
                raise ValueError('evaluation_corpus_changed')
            corpus_fingerprint = current_corpus
            labels = case.get('candidate_labels', {})
            if not args.collect_only and (set(labels) != {m['chunk_id'] for m in result['matches']}
                    or any(type(v) is not bool for v in labels.values())):
                raise ValueError('complete_candidate_labels_required')
            after = grade(case, result['matches'][:5], revision, run)
            candidate_grade = grade(case, result['candidate_order'], revision, run)
            row = {**after, 'split': case['split'], 'relevant': case['relevant'],
                   'top1_required': case.get('top1_required', False),
                   'top1_relevant': any(rank == 1 for rank in after['group_ranks']),
                   'candidate_complete': candidate_grade['evidence_complete'],
                   'score': result['matches'][0].get('rerank_score') if result['matches'] else None,
                   'seconds': time.perf_counter() - started, 'rerank_seconds': result['rerank_seconds'],
                   'before_rerank': result['candidate_order'],
                   'after_rerank': [{k: m.get(k) for k in ('chunk_id', 'chunk_index', 'revision_id', 'run_id', 'rerank_score', 'cosine_distance')}
                                    for m in result['matches']]}
            row.update(_case=case, _revision=revision, _run=run)
            for match in row['after_rerank']:
                match['relevant'] = labels.get(match['chunk_id'])
            rows.append(row)
        calibration = calibrate(rows) if not args.collect_only else {'threshold': None, 'holdout_passed': False}
        # Exercise the public filtering path for latency, with a provisional in-memory
        # threshold only. No profile is published unless every later gate passes.
        if calibration['threshold'] is not None:
            service._reranker.profile = dict(fingerprint=service._reranker.fingerprint,
                corpus_fingerprint=corpus_fingerprint, threshold=calibration['threshold'], holdout_passed=True)
        durations = []
        for index in range(args.requests):
            started = time.perf_counter()
            probe = await service.retrieve(entries[index % len(entries)][0]['question'], 5)
            if probe.get('corpus_fingerprint') != corpus_fingerprint:
                raise ValueError('evaluation_corpus_changed')
            durations.append(time.perf_counter() - started)
        for package, cases_path, case_bytes, record, payload, files, expected, before in snapshots:
            if (await database_state(record, payload, expected) != before
                    or packages.snapshot_files(package) != files or cases_path.read_bytes() != case_bytes):
                raise ValueError('evaluation_inputs_changed')
        if args.manifest.read_bytes() != manifest_bytes or model_fingerprint(args.model_path) != fingerprint:
            raise ValueError('evaluation_inputs_changed')
        import psutil
        memory = psutil.Process().memory_info()
        summary = summarize(rows)
        summary['candidate_pool_misses'] = sum(r['relevant'] and not r['candidate_complete'] for r in rows)
        summary['post_filter_evidence_misses'] = sum(r['relevant'] and r['evidence_complete'] is not True for r in rows)
        summary['false_empty_count'] = sum(r.get('false_empty', False) for r in rows)
        gates = {'evidence': summary['target_met'],
                 'required_top1': all(r.get('required_top1_passed', False) for r in rows if r['top1_required']),
                 'relevance': calibration['holdout_passed'], 'latency': percentile(durations, .95) <= 5}
        # Private questions and internal grading inputs never enter reports.
        for row in rows:
            for key in ('_case', '_revision', '_run'):
                row.pop(key)
        return {'corpus_fingerprint': corpus_fingerprint, 'collect_only': args.collect_only,
                'fingerprint': service._reranker.fingerprint, 'model_files_sha256': fingerprint,
                'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(), 'cases': rows,
                'summary': summary, 'calibration': calibration, 'gates': gates,
                'rollout_ready': all(gates.values()), 'candidate_count': settings.candidate_count,
                'p50_seconds': percentile(durations, .5), 'p95_seconds': percentile(durations, .95),
                'latency_requests': len(durations), 'peak_rss_bytes': getattr(memory, 'peak_wset', None),
                'rss_bytes': memory.rss, 'platform': platform.platform(), 'cpu': platform.processor(),
                'python': platform.python_version(), 'database_and_packages_unchanged': True}
    finally:
        await service.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--model-revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--collect-only', action='store_true', help='Collect candidate identities for source labeling; never emits a profile.')
    parser.add_argument('--requests', type=int, default=100)
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        validate_outputs(args)
        async def run():
            try:
                return await benchmark(args)
            finally:
                await close_database()
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            report = run_async(run())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        if report['rollout_ready']:
            profile = args.output.with_suffix('.profile.json')
            if profile.exists():
                raise ValueError('profile_already_exists')
            profile.write_text(json.dumps({'schema_version': 2, 'fingerprint': report['fingerprint'],
                'corpus_fingerprint': report['corpus_fingerprint'],
                'threshold': report['calibration']['threshold'], 'holdout_passed': True}), encoding='utf-8')
        print(json.dumps({k: report[k] for k in ('gates', 'rollout_ready', 'p50_seconds', 'p95_seconds', 'peak_rss_bytes')}))
        return 0 if report['rollout_ready'] else 1
    except Exception:
        print('Read-only online benchmark failed; no diagnostic content emitted.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
