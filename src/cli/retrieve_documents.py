"""Retrieve public source chunks for a question; no answer generation."""
import argparse
import json
import logging
import os
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from src.retrieval.config import add_arguments
from src.intent.config import add_arguments as add_intent_arguments


async def run(args):
    """Compatibility wrapper for the shared one-shot retrieval runner."""
    from src.retrieval.runner import run as retrieve
    return await retrieve(args)


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
