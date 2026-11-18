"""Retrieve public source chunks for a question; no answer generation."""
import argparse
import json
import logging
import os
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from src.retrieval.config import add_arguments
from src.retrieval.semantic_config import add_arguments as add_semantic_arguments


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
    add_semantic_arguments(cli)
    args = cli.parse_args(argv)
    from src.database.runtime import run_async
    with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        result, code = run_async(run(args))
    print(json.dumps(result, ensure_ascii=True, indent=None if args.json else 2, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
