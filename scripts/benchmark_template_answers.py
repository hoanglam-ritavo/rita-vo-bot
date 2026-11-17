"""Read-only exposed Type A regression through the real answer service, no listener."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
from statistics import median
import time
from src.api.config import load_settings
from src.api.contracts import ApiError
from src.api.service import ApiService
from src.database.runtime import run_async
from src.paths import REPOSITORY_ROOT
from src.answering.catalog import sha_bytes


def private_path(value):
    path = Path(value).resolve()
    root = (REPOSITORY_ROOT / '.tmp').resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('path_outside_tmp')
    return path


def load_cases(path):
    data = json.loads(private_path(path).read_text(encoding='utf-8'))
    if (not isinstance(data, list) or not 1 <= len(data) <= 1000
            or any(not isinstance(c, dict) or not isinstance(c.get('text'), str)
                   or not c['text'].strip() or len(c['text']) > 8000 for c in data)):
        raise ValueError('invalid_cases')
    return data


async def collect(settings, cases, output):
    service = ApiService(settings)
    report = {'status':'failed','acceptance':False,'rows':[]}
    paths = (settings.answer.catalog_path, settings.semantic.artifact_path)
    identity = [sha_bytes(Path(path).read_bytes()) for path in paths]
    try:
        started = time.perf_counter()
        await service.start()
        report['startup_seconds'] = time.perf_counter() - started
        if not service.answers.available:
            raise ValueError('answer_unavailable')
        for index, case in enumerate(cases):
            started, cpu = time.perf_counter(), time.process_time()
            try:
                response = await service.answers.answer(case['text'], settings.top_k)
                body = response.model_dump()
                row = {'index':index, 'status':body['status'], 'reason':body['fallback_reason'],
                       'claims':len(body['claims']), 'sources':len(body['sources']), 'timings':body['timings'],
                       'process_cpu_seconds':time.process_time() - cpu}
            except ApiError as exc:
                body = {'error':exc.code}
                row = {'index':index,'error':exc.code}
            elapsed = time.perf_counter() - started
            row['wall_seconds'] = elapsed
            if 'process_cpu_seconds' in row:
                row['process_cpu_percent_of_machine'] = 100 * row['process_cpu_seconds'] / (elapsed * (os.cpu_count() or 1))
            with private_path(output / f'case-{index:03}.private.json').open('x', encoding='utf-8') as stream:
                json.dump({'case':case,'response':body},stream,ensure_ascii=False,indent=2)
            report['rows'].append(row)
        if identity != [sha_bytes(Path(path).read_bytes()) for path in paths]:
            raise ValueError('artifacts_changed')
        times = sorted(row['wall_seconds'] for row in report['rows'])
        report.update(status='completed', catalog_sha256=identity[0], lexicon_sha256=identity[1],
            summary={'cases':len(times), 'statuses':dict(Counter(r.get('status','error') for r in report['rows'])),
                     'median_seconds':median(times),'p95_seconds':times[max(0, (95*len(times)+99)//100-1)],
                     'cpu_logical_count':os.cpu_count()},
            notice='Exposed local regression, not independent quality or production p95 acceptance.')
    except Exception:
        report['error'] = 'regression_unavailable_or_changed'
    finally:
        await service.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases)
        cases_hash = sha_bytes(private_path(args.cases).read_bytes())
        output = private_path(args.output)
        output.mkdir(parents=True,exist_ok=False)
        report = run_async(collect(load_settings(), cases, output))
        report['cases_sha256'] = cases_hash
        if sha_bytes(private_path(args.cases).read_bytes()) != cases_hash:
            report.update(status='failed',error='cases_changed')
        with (output/'report.json').open('x',encoding='utf-8') as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2)
        print(json.dumps({'status':report['status'],'summary':report.get('summary')}))
        return 0 if report['status']=='completed' else 1
    except Exception:
        print('template_regression_failed code=invalid_input_or_runtime')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
