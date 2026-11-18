"""Explicit offline artifact source import, deterministic build and read-only check."""
import argparse
import json
from pathlib import Path

from src.artifact_authoring.sources import SourceError
from src.artifact_authoring.workflow import build, import_pair


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    commands.add_parser('build')
    commands.add_parser('impact', help='Read-only dependency report; no source text or database IO.')
    commands.add_parser('refresh', help='Relink unique unchanged evidence and refresh coverage; then build/check.')
    command = commands.add_parser('import')
    command.add_argument('--lexicon', required=True, type=Path)
    command.add_argument('--catalog', required=True, type=Path)
    command.add_argument('--output', required=True, type=Path,
                         help='New directory below repository .tmp; never current sources.')
    args = parser.parse_args(argv)
    try:
        if args.command in ('impact', 'refresh'):
            from src.artifact_authoring.impact import inspect, refresh
            result = inspect()[0] if args.command == 'impact' else refresh()
        else:
            result = (import_pair(args.lexicon, args.catalog, args.output) if args.command == 'import'
                      else build(check=args.command == 'check'))
        print(json.dumps(result))
        return 0
    except (ValueError, OSError) as exc:
        # Validators can contain source text: never emit raw validation messages.
        code = str(exc) if isinstance(exc, SourceError) else 'artifact_validation_or_io_failed'
        print(json.dumps({'status': 'failed', 'code': code}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
