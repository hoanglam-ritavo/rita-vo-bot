"""Retrieve public source chunks for a question; no answer generation."""
import argparse
import json
import logging
import os
import sys
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from src.retrieval import RetrievalService, RetrievalError
from src.retrieval.service import validate_request
from src.retrieval.config import add_arguments, from_arguments
from src.intent.config import add_arguments as add_intent_arguments, from_arguments as intent_from_arguments


async def run(args):
    from src.database import close_database
    service = None
    result, code = None, 0
    timings = {'model_initialization_seconds': None, 'retrieval_seconds': None}
    try:
        validate_request(args.question, args.top_k)
        service = RetrievalService(model_path=args.model_path, model_revision=args.model_revision,
                                   rerank=from_arguments(args), intent=intent_from_arguments(args))
        started = time.perf_counter()
        await service.start()
        timings['model_initialization_seconds'] = time.perf_counter() - started
        started = time.perf_counter()
        result = await service.retrieve(args.question, args.top_k)
        timings['rerank_seconds'] = result.get('rerank_seconds', 0.0)
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
    cli.add_argument('--top-k', type=int, default=5, help='Maximum accepted excerpts, from 1 to 20 (default: 5); may return fewer or none.')
    cli.add_argument('--json', action='store_true')
    add_arguments(cli)
    add_intent_arguments(cli)
    args = cli.parse_args(argv)
    from src.database.runtime import run_async
    # Bind only the safe observation logger to real stderr before silencing model output.
    intent_logger = logging.getLogger('src.intent.runtime')
    handler = logging.StreamHandler(sys.stderr) if args.intent_mode == 'observe' else None
    previous_level, previous_propagation = intent_logger.level, intent_logger.propagate
    try:
        if handler is not None:
            intent_logger.addHandler(handler)
            intent_logger.setLevel(logging.INFO)
            intent_logger.propagate = False
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            result, code = run_async(run(args))
    finally:
        if handler is not None:
            intent_logger.removeHandler(handler)
            handler.close()
            intent_logger.setLevel(previous_level)
            intent_logger.propagate = previous_propagation
    print(json.dumps(result, ensure_ascii=True, indent=None if args.json else 2, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
