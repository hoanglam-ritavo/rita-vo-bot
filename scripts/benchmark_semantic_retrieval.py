"""Read-only paired baseline/semantic/intent-assist benchmark and calibration.

Private datasets/reports stay under .tmp. Collection emits IDs, never questions
or excerpts. Full runs require source-reviewed union labels and >=300 cases.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import platform
import time
from uuid import UUID

from src.database import close_database, session_scope
from src.database.runtime import run_async
from src.ingestion import document_packages as packages
from src.ingestion.approved_publication import configuration
from src.ingestion.embeddings import model_fingerprint
from src.intent import IntentSettings
from src.paths import REPOSITORY_ROOT
from src.retrieval import RetrievalService
from src.retrieval.config import add_arguments, from_arguments
from src.retrieval.semantic_evaluation import (check_labels, compare, digest, private_path,
    summarize, threshold_for, validate_dataset)
from src.retrieval.semantics import SemanticSettings
from src.retrieval.scoring import FEATURES, validate_scoring
from src.retrieval.reranking import MODEL_REVISION


def path_at_root(value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


def load_inputs(args):
    """Review/split validation precedes models, database access and output writes."""
    args.dataset = private_path(args.dataset)
    args.output = private_path(args.output)
    data_bytes = args.dataset.read_bytes()
    data = json.loads(data_bytes)
    cases = validate_dataset(data, collect_only=args.collect_only)
    if not args.collect_only and args.requests < 100:
        raise ValueError('insufficient_latency_requests')
    if args.requests < 0:
        raise ValueError('invalid_latency_requests')
    if not args.rerank or args.rerank_profile is not None:
        raise ValueError('semantic_benchmark_requires_uncalibrated_reranking')
    pins = data.get('packages')
    if not isinstance(pins, list) or not pins:
        raise ValueError('semantic_package_pins_required')
    snapshots, references, seen = [], {}, set()
    for pin in pins:
        if not isinstance(pin, dict) or set(pin) != {'path', 'sha256'}:
            raise ValueError('invalid_semantic_package_pin')
        package = path_at_root(pin['path'])
        if package in seen:
            raise ValueError('duplicate_semantic_package_pin')
        seen.add(package)
        record, payload, files = packages.load_package(REPOSITORY_ROOT, package, eligible=True)
        if packages.files_hash(files) != pin['sha256']:
            raise ValueError('semantic_package_pin_changed')
        references[(record['document_id'], record['revision_id'])] = len(payload['chunks'])
        snapshots.append((package, record, payload, files))
    if seen != {path.resolve() for path in packages.package_paths(REPOSITORY_ROOT)}:
        raise ValueError('whole_corpus_package_pins_required')
    for case in cases:
        for group in case['required_groups']:
            for ref in group:
                count = references.get((ref['document_id'], ref['revision_id']), 0)
                if ref['chunk_index'] >= count:
                    raise ValueError('unpinned_semantic_evidence_reference')
    artifacts = [path_at_root(args.semantic_artifact)]
    if args.intent_artifact:
        artifacts.append(path_at_root(args.intent_artifact))
    scoring_path = getattr(args, 'scoring_candidate', None)
    training_path = getattr(args, 'scoring_training_report', None)
    if bool(scoring_path) != bool(training_path):
        raise ValueError('scoring_training_report_required')
    if scoring_path:
        scoring_path, training_path = private_path(scoring_path), private_path(training_path)
        scoring = validate_scoring(json.loads(scoring_path.read_bytes()))
        training = json.loads(training_path.read_bytes())
        groups = training.get('development_intent_groups')
        case_ids = training.get('development_case_ids')
        if (training.get('scoring_digest') != scoring['digest']
                or training.get('training_only') is not True
                or not isinstance(groups, list) or not groups
                or not isinstance(case_ids, list) or not case_ids):
            raise ValueError('invalid_scoring_training_report')
        if any(case['split'] in ('calibration', 'holdout') and
                (case['intent_group'] in groups or case['id'] in case_ids) for case in cases):
            raise ValueError('scoring_development_acceptance_leak')
        artifacts.extend([scoring_path, training_path])
    model_paths = [path_at_root(args.model_path), from_arguments(args).model_path.resolve()]
    protected = {args.dataset, *artifacts}
    outputs = [args.output, *[args.output.with_name(args.output.stem + f'.{arm}.profile.json')
                              for arm in ('baseline', 'semantic', 'intent')]]
    for output in outputs:
        if (output.exists() or output in protected or any(output.is_relative_to(path) for path in model_paths)
                or any(output.is_relative_to(snapshot[0]) for snapshot in snapshots)):
            raise ValueError('semantic_output_overwrites_input')
    artifact_bytes = {path: path.read_bytes() for path in artifacts}
    return data, cases, snapshots, data_bytes, artifact_bytes


async def publication_snapshot(snapshots, revision, fingerprint):
    """Pin immutable active publication configs; never migrate or write tables."""
    from sqlalchemy import select, text
    from src.database.models import DocumentRevision, IngestionRun
    from src.retrieval.storage import eligibility
    result = []
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        for _, record, payload, files in snapshots:
            run = await db.scalar(select(IngestionRun).join(DocumentRevision,
                IngestionRun.revision_id == DocumentRevision.id).where(
                *eligibility(revision, fingerprint),
                DocumentRevision.id == UUID(record['revision_id']),
                DocumentRevision.document_id == UUID(record['document_id'])))
            expected = configuration(payload, revision)
            expected.update(package_sha256=packages.files_hash(files),
                            metadata_sha256=packages.sha(files['metadata.json']), model_files_sha256=fingerprint)
            if (run is None or run.chunk_count != len(payload['chunks'])
                    or any(run.configuration.get(key) != value for key, value in expected.items())):
                raise ValueError('semantic_publication_pin_mismatch')
            result.append((str(run.id), str(run.revision_id), run.configuration))
    return digest(result)


def pipeline_settings(args):
    semantic = SemanticSettings('expand', path_at_root(args.semantic_artifact))
    result = {'baseline': (SemanticSettings(), IntentSettings()),
              'semantic': (semantic, IntentSettings())}
    if args.intent_artifact:
        result['intent'] = (semantic, IntentSettings('assist', path_at_root(args.intent_artifact)))
    return result


async def benchmark(args, inputs=None):
    data, cases, snapshots, data_bytes, artifact_bytes = inputs or load_inputs(args)
    model_path = path_at_root(args.model_path)
    fingerprint = model_fingerprint(model_path)
    before = await publication_snapshot(snapshots, args.model_revision, fingerprint)
    services, results, latency = {}, {}, {}
    corpus = None
    report = None
    try:
        for arm, (semantic, intent) in pipeline_settings(args).items():
            service = RetrievalService(model_path=model_path, model_revision=args.model_revision,
                device='cpu', rerank=replace(from_arguments(args), profile_path=None,
                    scoring_path=private_path(args.scoring_candidate) if getattr(args, 'scoring_candidate', None) else None),
                semantic=semantic, intent=intent)
            services[arm] = service
            results[arm], latency[arm] = [], []
            await service.start()
            if getattr(args, 'scoring_candidate', None):
                expected_scoring = validate_scoring(json.loads(artifact_bytes[private_path(args.scoring_candidate)]))
                if service._reranker.scoring != expected_scoring:
                    raise ValueError('semantic_evaluation_inputs_changed')
            if arm == 'intent' and service._intent.artifact is None:
                raise ValueError('accepted_intent_artifact_required_for_benchmark')
        def verify_corpus(result):
            nonlocal corpus
            current = result.get('corpus_fingerprint')
            if not current or corpus is not None and corpus != current:
                raise ValueError('semantic_evaluation_corpus_changed')
            corpus = current
        # Warm each independently, then alternate request order to reduce order bias.
        for service in services.values():
            verify_corpus(await service._retrieve(cases[0]['question'], 5, assessment=False))
        arms = list(services)
        for index, case in enumerate(cases):
            case_results = {}
            for arm in arms if index % 2 == 0 else list(reversed(arms)):
                result = await services[arm]._retrieve(case['question'], 5, assessment=False,
                    collect_scoring_features=getattr(args, 'collect_features', False))
                verify_corpus(result)
                if (result.get('status') == 'model_mismatch'
                        or not result.get('reranker_fingerprint') or 'candidate_order' not in result):
                    raise ValueError('semantic_candidate_collection_unavailable')
                case_results[arm] = result
                results[arm].append(result)
            if not args.collect_only:
                check_labels(case, case_results)
        thresholds = {arm: None if args.collect_only else threshold_for(cases, rows)
                      for arm, rows in results.items()}
        for arm, service in services.items():
            if thresholds[arm] is not None:
                service._reranker.profile = {'schema_version': 2, 'fingerprint': service._reranker.fingerprint,
                    'corpus_fingerprint': corpus, 'threshold': thresholds[arm], 'holdout_passed': True}
                if getattr(service._reranker, 'scoring', None) is not None:
                    service._reranker.profile.update(schema_version=3, scoring=service._reranker.scoring)
        if not args.collect_only:
            for index in range(args.requests):
                question = cases[index % len(cases)]['question']
                for arm in arms if index % 2 == 0 else list(reversed(arms)):
                    started = time.perf_counter()
                    result = await services[arm].retrieve(question, 5)
                    latency[arm].append(time.perf_counter() - started)
                    verify_corpus(result)
        for package, _, _, files in snapshots:
            if packages.snapshot_files(package) != files:
                raise ValueError('semantic_evaluation_inputs_changed')
        if (args.dataset.read_bytes() != data_bytes or model_fingerprint(model_path) != fingerprint
                or any(path.read_bytes() != value for path, value in artifact_bytes.items())
                or await publication_snapshot(snapshots, args.model_revision, fingerprint) != before):
            raise ValueError('semantic_evaluation_inputs_changed')
        arms_report = {}
        for arm in arms:
            base = {'fingerprint': services[arm]._reranker.fingerprint}
            if args.collect_only:
                base.update(candidate_collection=[{'id': case['id'],
                    'before_rerank': result['candidate_order'],
                    'after_rerank': [{key: match[key] for key in
                        ('chunk_id', 'document_id', 'revision_id', 'chunk_index', 'run_id', 'rerank_score', 'cosine_distance')}
                        for match in result['matches']]} for case, result in zip(cases, results[arm])])
                if getattr(args, 'collect_features', False):
                    for row, result in zip(base['candidate_collection'], results[arm]):
                        row['scoring_features'] = result['scoring_features']
            else:
                base.update(summarize(cases, results[arm], latency[arm], thresholds[arm]))
            arms_report[arm] = base
        comparisons = {} if args.collect_only else {'semantic_vs_baseline': compare(arms_report['baseline'], arms_report['semantic'])}
        if not args.collect_only and 'intent' in arms_report:
            comparisons['intent_vs_semantic'] = compare(arms_report['semantic'], arms_report['intent'])
        report = {'schema_version': 1, 'collect_only': args.collect_only,
            'dataset_sha256': hashlib.sha256(data_bytes).hexdigest(),
            'model_files_sha256': fingerprint, 'corpus_fingerprint': corpus,
            'publication_snapshot': before, 'artifacts': {str(index): hashlib.sha256(value).hexdigest()
                         for index, value in enumerate(artifact_bytes.values())},
            'pipelines': arms_report, 'comparisons': comparisons,
            'rollout_ready': not args.collect_only and comparisons['semantic_vs_baseline']['promotion_ready'],
            'database_and_packages_unchanged': True,
            'python': platform.python_version(), 'platform': platform.platform()}
        if getattr(args, 'collect_features', False):
            report['feature_names'] = list(FEATURES)
        if getattr(args, 'scoring_candidate', None):
            report['scoring'] = validate_scoring(json.loads(artifact_bytes[private_path(args.scoring_candidate)]))
            report['release_configuration'] = {
                'semantic_mode': 'expand', 'intent_mode': 'assist' if args.intent_artifact else 'off',
                'candidate_count': 30, 'batch_size': from_arguments(args).batch_size,
                'model_revision': args.model_revision, 'rerank_model_revision': MODEL_REVISION,
                'semantic_artifact_sha256': hashlib.sha256(artifact_bytes[path_at_root(args.semantic_artifact)]).hexdigest(),
                'scoring_digest': report['scoring']['digest'],
                'training_report_sha256': hashlib.sha256(artifact_bytes[private_path(args.scoring_training_report)]).hexdigest()}
    finally:
        # A cleanup failure must prevent writing any accepted profile/report.
        errors = []
        for service in reversed(list(services.values())):
            try:
                await service.close()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise ValueError('semantic_evaluation_cleanup_failed') from None
    return report


def write_report(args, report):
    outputs = {args.output: report}
    if not report['collect_only']:
        for arm, result in report['pipelines'].items():
            passed = result['absolute_gates_passed']
            if arm == 'semantic':
                passed = passed and report['comparisons']['semantic_vs_baseline']['promotion_ready']
            elif arm == 'intent':
                passed = (passed
                    and report['comparisons']['semantic_vs_baseline']['promotion_ready']
                    and report['comparisons']['intent_vs_semantic']['promotion_ready'])
            if passed:
                outputs[args.output.with_name(args.output.stem + f'.{arm}.profile.json')] = {
                    'schema_version': 2, 'fingerprint': result['fingerprint'],
                    'corpus_fingerprint': report['corpus_fingerprint'],
                    'threshold': result['threshold'], 'holdout_passed': True}
                if report.get('scoring') is not None:
                    outputs[args.output.with_name(args.output.stem + f'.{arm}.profile.json')].update(
                        schema_version=3, scoring=validate_scoring(report['scoring']))
    for path in outputs:
        if path.exists():
            raise ValueError('semantic_output_already_exists')
    for path, value in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--model-revision', required=True)
    parser.add_argument('--semantic-artifact', type=Path, required=True)
    parser.add_argument('--intent-artifact', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--collect-only', action='store_true')
    parser.add_argument('--requests', type=int, default=100)
    parser.add_argument('--collect-features', action='store_true', help='Include private numeric development features in collection.')
    parser.add_argument('--scoring-candidate', type=Path, help='Private fitted scoring coefficients; never an accepted profile.')
    parser.add_argument('--scoring-training-report', type=Path, help='Matching development provenance and family-isolation ledger.')
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        inputs = load_inputs(args)
        async def run():
            try:
                return await benchmark(args, inputs)
            finally:
                await close_database()
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            report = run_async(run())
        write_report(args, report)
        print(json.dumps({'collect_only': report['collect_only'], 'rollout_ready': report['rollout_ready'],
                          'comparisons': report['comparisons']}))
        return 0 if report['collect_only'] or report['rollout_ready'] else 1
    except Exception:
        print(json.dumps({'code': 'semantic_benchmark_failed', 'rollout_ready': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
