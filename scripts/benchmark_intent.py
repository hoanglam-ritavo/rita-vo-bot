"""Read-only paired retrieval benchmark; the only intent artifact promotion path.

Requires reviewed data, passed intent quality gates and a matching retrieval
relevance profile. Never bypasses passage filtering or writes publication data.
"""
import argparse
import copy
import json
import time

import numpy as np

from scripts.intent_contract import read_json, write_new, validate_dataset, dataset_hash, check_outputs, safe_code
from src.database import close_database
from src.database.runtime import run_async
from src.ingestion import embeddings
from src.intent import IntentSettings
from src.intent.runtime import IntentObserver, GATES, digest, identity, validate_artifact, classify
from src.retrieval import RetrievalService
from src.retrieval.config import add_arguments, from_arguments
from src.retrieval.service import validate_request
from src.retrieval.reranking import query_variants


def comparable(result):
    return {k: v for k, v in result.items() if k != 'rerank_seconds'}


async def benchmark(args):
    check_outputs(args.output_prefix, ('-benchmark.json', '-accepted.json'), args.model_path)
    data, artifact, report = (read_json(p) for p in (args.dataset, args.candidate, args.training_report))
    cases = validate_dataset(data)
    acceptance = artifact.get('acceptance', {})
    if (acceptance.get('dataset_sha256') != dataset_hash(data)
            or acceptance.get('report_sha256') != digest(report)
            or report.get('dataset_sha256') != dataset_hash(data)
            or report.get('candidate_payload_sha256') != digest({k: v for k, v in artifact.items() if k != 'acceptance'})
            or acceptance.get('reviewed') is not True
            or any(report.get('gates', {}).get(k) is not True for k in GATES if k != 'latency')
            or acceptance.get('gates') != report['gates']):
        raise ValueError('intent_quality_not_accepted')
    rerank = from_arguments(args)
    if not rerank.enabled or rerank.profile_path is None or args.requests < 200:
        raise ValueError('calibrated_reranking_and_200_requests_required')
    service = RetrievalService(model_path=args.model_path, model_revision=args.model_revision,
                               device='cpu', rerank=rerank)
    initial_artifact = digest(artifact)
    try:
        await service.start()
        validate_artifact(artifact, identity(args.model_revision, service._fingerprint, True), require_accepted=False)
        observer = IntentObserver(IntentSettings('observe'))
        # Offline benchmark alone can attach an unpromoted candidate; no public bypass flag.
        observer.artifact, observer.version = artifact, initial_artifact
        disabled = IntentObserver(IntentSettings())
        selected = [c for c in cases if c['split'] == 'holdout']
        durations, head_times, rows = {'off': [], 'observe': []}, [], []
        fingerprints = set()

        async def request(case, mode):
            service._intent = observer if mode == 'observe' else disabled
            started = time.perf_counter()
            result = await service.retrieve(case['question'], 5)
            elapsed = time.perf_counter() - started
            # Empty below-threshold is valid. Missing eligibility/profile is not a benchmark.
            if (result['status'] not in ('matches_found', 'no_matches')
                    or result.get('fallback_reason') in ('relevance_unavailable', 'no_candidates')
                    or result.get('corpus_fingerprint') is None):
                raise ValueError('retrieval_calibration_unavailable')
            fingerprints.add(result['corpus_fingerprint'])
            return result, elapsed

        for mode in ('off', 'observe'):
            await request(selected[0], mode)
        equal = True
        for i in range(args.requests):
            case = selected[i % len(selected)]
            results = {}
            for mode in (('off', 'observe') if i % 2 == 0 else ('observe', 'off')):
                results[mode], elapsed = await request(case, mode)
                durations[mode].append(elapsed)
            same = comparable(results['off']) == comparable(results['observe'])
            equal = equal and same
            rows.append({'id': case['id'], 'same': same, 'off_seconds': durations['off'][-1],
                         'observe_seconds': durations['observe'][-1]})
        # Head-only timing outside the full-path measurement; no DB or extra production encode.
        question = query_variants(validate_request(selected[0]['question'], 5))[0]
        vector = await service._worker(service._encode, question)
        for _ in range(args.requests):
            started = time.perf_counter()
            classify(artifact, vector)
            head_times.append(time.perf_counter() - started)
        overhead = float(np.percentile(np.asarray(durations['observe']) - durations['off'], 95))
        head_p95 = float(np.percentile(head_times, 95))
        if (len(fingerprints) != 1 or embeddings.model_fingerprint(args.model_path) != service._fingerprint
                or dataset_hash(read_json(args.dataset)) != dataset_hash(data)
                or digest(read_json(args.candidate)) != initial_artifact
                or digest(read_json(args.training_report)) != digest(report)):
            raise ValueError('benchmark_inputs_changed')
        gates = dict(report['gates'], latency=overhead <= .01 and head_p95 <= .01, parity=equal and report['gates']['parity'])
        output = {'schema_version': 1, 'dataset_sha256': dataset_hash(data),
                  'candidate_sha256': initial_artifact, 'training_report_sha256': digest(report),
                  'corpus_fingerprint': next(iter(fingerprints)), 'requests_per_mode': args.requests,
                  'p95_seconds': {k: float(np.percentile(v, 95)) for k, v in durations.items()},
                  'paired_overhead_p95_seconds': overhead, 'head_p95_seconds': head_p95,
                  'gates': gates, 'cases': rows}
        # Persist nothing until shutdown succeeds, including partial-startup cleanup.
    finally:
        try:
            await service.close()
        finally:
            await close_database()
    write_new(args.output_prefix + '-benchmark.json', output)
    if all(gates.values()):
        approved = copy.deepcopy(artifact)
        approved['acceptance'] = {'reviewed': True, 'dataset_sha256': dataset_hash(data),
                                  'report_sha256': digest(output), 'gates': gates}
        write_new(args.output_prefix + '-accepted.json', approved)
    return {'status': 'accepted' if all(gates.values()) else 'gates_failed', 'gates': gates}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'candidate', 'training-report', 'model-path', 'model-revision', 'output-prefix'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--requests', type=int, default=200)
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        result = run_async(benchmark(args))
        print(json.dumps(result))
        return 0 if result['status'] == 'accepted' else 1
    except Exception as exc:
        print(json.dumps({'status': 'error', 'code': safe_code(exc, 'intent_benchmark_failed')}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
