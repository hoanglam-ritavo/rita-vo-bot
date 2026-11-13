"""Offline stage-one experiment. No API, migrations, publication, or automatic downloads."""
import argparse
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import time

from scripts.answer_evaluation_contract import evaluate_gates, load_cases, private_path, sha_file, write_new
from scripts.answer_evaluation_runtime import LocalRuntime, evaluate_question
from src.api.config import load_settings
from src.database import close_database
from src.database.runtime import run_async
from src.paths import REPOSITORY_ROOT
from src.retrieval import RetrievalService
from src.retrieval.reranking import load_profile


def preflight(args):
    """Static prerequisites only. Success is NOT corpus validation or rollout approval."""
    blockers = []
    try:
        settings = load_settings()
        if not settings.rerank.enabled:
            blockers.append('reranking_disabled')
        profile = load_profile(settings.rerank.profile_path) if settings.rerank.profile_path else None
        if profile is None or profile.get('holdout_passed') is not True:
            blockers.append('validated_retrieval_profile_missing')
    except (ValueError, OSError):
        blockers.append('invalid_retrieval_configuration')
    if args.cases is None:
        blockers.append('source_reviewed_60_case_manifest_missing')
    else:
        try:
            load_cases(args.cases)
        except (ValueError, OSError, KeyError, TypeError):
            blockers.append('case_manifest_invalid_or_stale')
    for name in ('runtime', 'model'):
        path, expected = getattr(args, name), getattr(args, name + '_sha256')
        if path is None or not path.is_file():
            blockers.append(name + '_file_missing')
        elif not expected or sha_file(path) != expected:
            blockers.append(name + '_hash_missing_or_mismatched')
    return {'schema_version': 1, 'status': 'blocked' if blockers else 'static_checks_passed',
            'blockers': blockers, 'integration_ready': False,
            'notice': 'No inference or database checks performed. Corpus compatibility and human review remain required.'}


def protect_artifact_directories(output, args):
    protected = []
    for key in ('model', 'runtime'):
        artifact = getattr(args, key, None)
        if artifact is not None:
            protected.append(artifact.resolve().parent)
    try:
        settings = load_settings()
        protected.extend([settings.model_path.resolve(), settings.rerank.model_path.resolve()])
    except (ValueError, OSError):
        pass  # Preflight reports invalid configuration without writing near supplied artifacts.
    if any(output.is_relative_to(directory) for directory in protected):
        raise ValueError('output_inside_model_or_runtime_directory')


async def run_experiment(args):
    import psutil
    cases_data = load_cases(args.cases)
    case_hash = sha_file(args.cases)
    settings = load_settings()
    retrieval = RetrievalService(model_path=settings.model_path, model_revision=settings.model_revision,
                                 device='cpu', rerank=settings.rerank)
    runtime = LocalRuntime(args.runtime, args.model, args.runtime_sha256, args.model_sha256, port=args.port)
    rows, samples = [], []
    peak_memory = psutil.virtual_memory().percent
    stop = asyncio.Event()
    async def sample_memory():
        nonlocal peak_memory
        while not stop.is_set():
            peak_memory = max(peak_memory, psutil.virtual_memory().percent)
            try:
                await asyncio.wait_for(stop.wait(), timeout=.1)
            except TimeoutError:
                pass
    monitor = asyncio.create_task(sample_memory())
    error, corpus, identity, measured = None, None, None, False
    profile_hash = sha_file(settings.rerank.profile_path)
    try:
        # Compare pinned authored content, spans and vectors against the published run.
        from scripts.evaluate_retrieval import database_state
        from src.ingestion import document_packages as packages
        from src.ingestion.approved_publication import configuration
        from src.ingestion.embeddings import model_fingerprint
        embedding_hash = model_fingerprint(settings.model_path)
        snapshots = []
        for relative in cases_data['packages']:
            package = REPOSITORY_ROOT / relative
            record, payload, files = packages.load_package(REPOSITORY_ROOT, package, eligible=True)
            expected = configuration(payload, settings.model_revision)
            expected.update(package_sha256=packages.files_hash(files), model_files_sha256=embedding_hash,
                            metadata_sha256=packages.sha(files['metadata.json']))
            before = await database_state(record, payload, expected)
            snapshots.append((record, payload, expected, before))
        await retrieval.start()
        # Validate real active-publication identity BEFORE starting a generator.
        async with asyncio.timeout(120):
            probe = await retrieval.retrieve(cases_data['cases'][0]['question'], 5)
        if probe.get('fallback_reason') == 'relevance_unavailable':
            raise ValueError('relevance_unavailable')
        corpus = probe.get('corpus_fingerprint')
        if probe['status'] == 'model_mismatch' or not corpus:
            raise ValueError('retrieval_corpus_unavailable')
        # No eligible evidence cannot be used as a successful generation benchmark.
        if probe['status'] == 'no_eligible_documents':
            raise ValueError('no_eligible_documents')
        identity = runtime.verify_files()
        await runtime.start()
        async with asyncio.timeout(120):
            # Fixed fictional warm-up: no holdout question or answer leaks into tuning.
            await runtime.complete('Return JSON only.', {'task': 'Return ok true.'},
                                   {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                                    'required': ['ok'], 'additionalProperties': False}, 32)
        measured = True
        for case in cases_data['cases']:
            started = time.perf_counter()
            row = {key: case[key] for key in ('id', 'split', 'category', 'critical')}
            try:
                async with asyncio.timeout(120):
                    value = await evaluate_question(case['question'], retrieval, runtime)
                if value['corpus_fingerprint'] != corpus:
                    raise ValueError('evaluation_corpus_changed')
                sample = {**{key: case[key] for key in ('id', 'question', 'expected', 'expected_status', 'source_refs')},
                          **value}
                row.update({key: val for key, val in value.items() if key not in ('baseline', 'draft', 'verdict')})
                samples.append(sample)
                row['error'] = None
            except TimeoutError:
                row.update(error='answer_timeout', accepted=False)
                error = 'answer_timeout'
            except Exception:
                # Never expose model text, source content, URLs or database errors in logs.
                row.update(error='experiment_failed', accepted=False)
                error = 'experiment_failed'
            row['seconds'] = time.perf_counter() - started
            rows.append(row)
            if error:
                # Stop the owned process, rather than assuming closing HTTP cancelled inference.
                break
        if (sha_file(args.cases) != case_hash or sha_file(settings.rerank.profile_path) != profile_hash
                or load_cases(args.cases) != cases_data or runtime.verify_files() != identity):
            error = 'evaluation_inputs_changed'
        # Detect publication changes after the last generated answer too.
        if not error:
            async with asyncio.timeout(120):
                final = await retrieval.retrieve(cases_data['cases'][0]['question'], 5)
            if final.get('corpus_fingerprint') != corpus or final.get('fallback_reason') == 'relevance_unavailable':
                error = 'evaluation_corpus_changed'
            for record, payload, expected, before in snapshots:
                if await database_state(record, payload, expected) != before:
                    error = 'evaluation_storage_changed'
            if model_fingerprint(settings.model_path) != embedding_hash:
                error = 'embedding_model_changed'
    except TimeoutError:
        error = 'preflight_or_warmup_timeout'
    except Exception:
        error = 'runtime_or_retrieval_preflight_failed'
    finally:
        try:
            await runtime.close()
        finally:
            try:
                await retrieval.close()
            finally:
                await close_database()
                stop.set()
                await monitor
    report = {'schema_version': 1, 'status': 'failed' if error else 'awaiting_manual_review',
              'error': error, 'cases_sha256': case_hash, 'profile_sha256': profile_hash,
              'runtime_identity': identity, 'runtime_measured': measured, 'corpus_fingerprint': corpus,
              'evaluator_sha256': hashlib.sha256(b''.join(Path(p).read_bytes() for p in (
                  __file__, Path(__file__).with_name('answer_evaluation_runtime.py'),
                  Path(__file__).with_name('answer_evaluation_contract.py')))).hexdigest(),
              'configuration': {'model': 'Qwen3-4B-Q4_K_M', 'context': 8192, 'threads': 6,
                                'generation_tokens': 1024, 'verification_tokens': 512,
                                'thinking': False, 'gpu_layers': 0, 'deadline_seconds': 120},
              'cases': rows, 'peak_system_memory_percent': peak_memory,
              'machine': {'cpu': platform.processor(), 'logical_cores': psutil.cpu_count(),
                          'physical_cores': psutil.cpu_count(logical=False),
                          'ram_bytes': psutil.virtual_memory().total},
              'integration_ready': False}
    return report, samples


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest='command', required=True)
    for name in ('preflight', 'run'):
        command = sub.add_parser(name)
        command.add_argument('--cases', type=Path)
        command.add_argument('--runtime', type=Path, help='Explicit local llama-server executable')
        command.add_argument('--runtime-sha256')
        command.add_argument('--model', type=Path, help='Explicit local Qwen3-4B Q4_K_M GGUF')
        command.add_argument('--model-sha256')
        command.add_argument('--port', type=int, default=8099)
        command.add_argument('--output', type=Path, required=True, help='New directory under .tmp')
    command = sub.add_parser('assess')
    command.add_argument('--report', type=Path, required=True)
    command.add_argument('--review', type=Path, required=True)
    command.add_argument('--output', type=Path, required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        output = private_path(args.output)
        if output.exists():
            raise ValueError('output_exists')
        protect_artifact_directories(output, args)
        if args.command == 'assess':
            report = json.loads(private_path(args.report).read_text(encoding='utf-8'))
            reviews = json.loads(private_path(args.review).read_text(encoding='utf-8'))
            if report.get('status') != 'awaiting_manual_review' or reviews.get('report_sha256') != sha_file(args.report):
                raise ValueError('review_does_not_match_successful_report')
            samples = private_path(args.report).with_name('samples.private.json')
            if report.get('samples_sha256') != sha_file(samples):
                raise ValueError('review_samples_changed')
            result = evaluate_gates(report, reviews)
            write_new(output, result)
            print(json.dumps(result))
            return 0 if result['integration_ready'] else 1
        check = preflight(args)
        write_new(output / 'preflight.json', check)
        if args.command == 'preflight' or check['blockers']:
            print(json.dumps(check))
            return 1 if check['blockers'] else 0
        # Models and dependencies can emit content: suppress their stdout/stderr during experiments.
        previous = logging.root.manager.disable
        try:
            logging.disable(logging.CRITICAL)
            with open(os.devnull, 'w') as null, redirect_stdout(null), redirect_stderr(null):
                report, samples = run_async(run_experiment(args))
        finally:
            logging.disable(previous)
        write_new(output / 'samples.private.json', samples)
        report['samples_sha256'] = sha_file(output / 'samples.private.json')
        write_new(output / 'report.json', report)
        review = {'report_sha256': sha_file(output / 'report.json'), 'user_accepted': False,
                  'cases': [{**{'id': row['id']}, **{key: None for key in (
                      'correct_and_complete', 'no_unsupported_claims', 'sources_valid',
                      'critical_passed', 'more_useful_than_excerpts')}} for row in report['cases']]}
        write_new(output / 'review.template.json', review)
        print(json.dumps({'status': report['status'], 'completed_cases': len(report['cases']),
                          'error': report['error'], 'integration_ready': False}))
        return 2 if report['error'] else 0
    except (ValueError, OSError, KeyError, TypeError):
        print(json.dumps({'status': 'invalid_evaluation_inputs', 'integration_ready': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
