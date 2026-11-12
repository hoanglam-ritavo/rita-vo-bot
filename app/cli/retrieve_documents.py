"""Retrieve public source chunks for a question; no answer generation."""
import argparse
import json
import os
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from app.retrieval import RetrievalService, RetrievalError
from app.retrieval.service import validate_request


async def run(args):
    from app.database import close_database
    service = None
    result, code = None, 0
    timings = {'model_initialization_seconds': None, 'retrieval_seconds': None}
    try:
        validate_request(args.question, args.top_k)
        service = RetrievalService(model_path=args.model_path, model_revision=args.model_revision)
        started = time.perf_counter()
        await service.start()
        timings['model_initialization_seconds'] = time.perf_counter() - started
        started = time.perf_counter()
        result = await service.retrieve(args.question, args.top_k)
        timings['retrieval_seconds'] = time.perf_counter() - started
        code = 1 if result['status'] == 'model_mismatch' else 0
    except RetrievalError as exc:
        result, code = {'status': 'error', 'code': exc.code, 'matches': []}, exc.exit_code
    except Exception:
        result, code = {'status': 'error', 'code': 'retrieval_failed', 'matches': []}, 2
    finally:
        try:
            if service is not None:
                await service.close()
            await close_database()
        except Exception:
            result, code = {'status': 'error', 'code': 'shutdown_failed', 'matches': []}, 2
    result = {**result, 'timings': timings}
    return result, code


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--question', required=True)
    cli.add_argument('--model-path', type=Path, required=True)
    cli.add_argument('--model-revision', required=True)
    cli.add_argument('--top-k', type=int, default=5, help='Number of candidates, from 1 to 20 (default: 5).')
    cli.add_argument('--json', action='store_true')
    args = cli.parse_args(argv)
    from app.database.runtime import run_async
    with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        result, code = run_async(run(args))
    print(json.dumps(result, ensure_ascii=True, indent=None if args.json else 2, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
