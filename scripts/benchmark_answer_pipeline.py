"""Bounded exposed local regression, never an acceptance study or HTTP server."""
import argparse
import asyncio
from contextlib import redirect_stdout
import json
from pathlib import Path
import re
from statistics import median
import time

from src.answering.acceptance import configuration
from src.answering.identity import DEADLINE_SECONDS, implementation_identity, retrieval_identity, sha_file
from src.answering.pipeline import evaluate_question
from src.api.config import load_settings
from src.api.service import ApiService
from src.database.runtime import run_async
from src.paths import REPOSITORY_ROOT
from src.semantics.corpus import snapshot_fingerprint


def private_path(value):
    path = Path(value).resolve()
    root = (REPOSITORY_ROOT / '.tmp').resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('path_outside_tmp')
    return path


def load_cases(path):
    data = json.loads(private_path(path).read_text(encoding='utf-8'))
    cases = data.get('cases')
    if (data.get('schema_version') != 1 or not isinstance(cases, list) or len(cases) != 8
            or any(not isinstance(case, dict) or set(case) != {'id', 'text'}
                   or not isinstance(case['id'], str) or not re.fullmatch(r'[a-z0-9-]{1,60}', case['id'])
                   or not isinstance(case['text'], str) or not 0 < len(case['text'].strip()) <= 8000
                   for case in cases)
            or len({case['id'] for case in cases}) != 8):
        raise ValueError('eight_unique_cases_required')
    return cases


def write_new(path, value):
    with private_path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def comparison(baseline, candidate):
    keys = ('cases_sha256', 'retrieval_identity', 'runtime_identity', 'configuration', 'packages')
    compatible = (baseline.get('status') == candidate.get('status') == 'completed'
                  and all(baseline.get(key) == candidate.get(key) for key in keys)
                  and len(set(baseline.get('corpora', []) + candidate.get('corpora', []))) == 1)
    return {'comparable': compatible, 'notice': 'Exposed regression only; not acceptance or p95 evidence.',
            'baseline': baseline.get('summary') if compatible else None,
            'candidate': candidate.get('summary') if compatible else None}


async def collect(settings, cases, output, *, service_factory=ApiService):
    service = service_factory(settings)
    report = {'status': 'failed', 'integration_ready': False, 'rows': [], 'corpora': [],
              'implementation': implementation_identity(), 'retrieval_identity': retrieval_identity(settings),
              'configuration': configuration(), 'packages': snapshot_fingerprint()}
    frozen = {key: report[key] for key in ('implementation', 'retrieval_identity', 'packages')}
    try:
        started = time.perf_counter()
        await service.start()
        report['startup_seconds'] = time.perf_counter() - started
        if not service.answers.available:
            raise ValueError('answer_unavailable')
        report['runtime_identity'] = await asyncio.to_thread(service.answers.runtime.verify_files)
        for case in cases:
            started = time.perf_counter()
            try:
                # Includes legacy raw print output in a private file, never the terminal.
                with (output / f"{case['id']}.stdout.private.txt").open('x', encoding='utf-8') as stream:
                    with redirect_stdout(stream):
                        async with asyncio.timeout(DEADLINE_SECONDS):
                            row = await evaluate_question(case['text'], service.retrieval,
                                service.answers.runtime, top_k=5, expected_corpus=service.answers.corpus,
                                allow_unassessed=not settings.relevance_filter_enabled)
                row['total_seconds'] = time.perf_counter() - started
                write_new(output / f"{case['id']}.private.json", {'case': case, 'result': row})
                report['corpora'].append(row.get('corpus_fingerprint'))
                report['rows'].append({'id': case['id'], **{key: row.get(key) for key in (
                    'accepted', 'status', 'reason', 'verification_diagnostics', 'generation_usage',
                    'verification_usage', 'retrieval_seconds', 'generation_seconds',
                    'verification_seconds', 'total_seconds')}})
                print(f"case_completed id={case['id']}", flush=True)
            except Exception:
                # Uncertain inference outcomes stop this run; never retry/reuse its runtime.
                report['rows'].append({'id': case['id'], 'error': 'regression_case_failed'})
                return report
        after = {'implementation': implementation_identity(), 'retrieval_identity': retrieval_identity(settings),
                 'packages': snapshot_fingerprint()}
        if (after != frozen or report['runtime_identity'] != await asyncio.to_thread(
                service.answers.runtime.verify_files) or None in report['corpora']
                or len(set(report['corpora'])) != 1):
            report['error'] = 'identity_changed'
            return report
        report['status'] = 'completed'
        report['summary'] = {'cases': len(report['rows']),
            'accepted': sum(bool(row['accepted']) for row in report['rows']),
            'median_total_seconds': median(row['total_seconds'] for row in report['rows'])}
        return report
    except Exception:
        report['error'] = 'regression_unavailable'
        return report
    finally:
        await service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--compare', type=Path, help='Baseline report.json, under .tmp.')
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
        cases_hash = sha_file(private_path(args.cases))
        output = private_path(args.output)
        baseline = (json.loads(private_path(args.compare).read_text(encoding='utf-8'))
                    if args.compare else None)
        settings = load_settings()
        output.mkdir(parents=True, exist_ok=False)
        report = run_async(collect(settings, cases, output))
        report['cases_sha256'] = cases_hash
        if sha_file(private_path(args.cases)) != cases_hash:
            report.update(status='failed', error='cases_changed')
        if baseline is not None:
            report['comparison'] = comparison(baseline, report)
        write_new(output / 'report.json', report)
        print(f"regression_finished status={report['status']}")
        return 0 if report['status'] == 'completed' else 1
    except Exception:
        print('regression_failed code=invalid_input_or_runtime')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
