"""Validate, preflight or prepare one offline intent classifier candidate."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import json
import os
import sys

from src.intent.preparation import execute


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('validate', 'preflight', 'run'))
    parser.add_argument('--config', required=True)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    terminal = sys.stderr
    def progress(stage):
        print('intent_preparation stage=' + stage, file=terminal, flush=True)
    with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        result, code = execute(args.command, args.config, progress)
    if args.json:
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    else:
        print('Intent preparation: ' + result['status'])
        if result.get('error_code'):
            print('Error: ' + result['error_code'])
        if result.get('run_directory'):
            print('Run: ' + result['run_directory'])
        if 'validation' in result:
            print('Training allowed: ' + str(result['validation']['training_allowed']))
            for error in result['validation']['errors']:
                print(json.dumps(error, ensure_ascii=True))
        if 'preflight' in result:
            print('Training allowed: ' + str(result['preflight']['training_allowed']))
            for check in result['preflight']['checks']:
                if check['status'] != 'passed':
                    print(json.dumps(check))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
