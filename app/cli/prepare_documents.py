"""Initialize, validate and revise agent-authored chunk packages."""
import argparse
from pathlib import Path
from app.paths import REPOSITORY_ROOT
from app.catalog.inventory import CatalogError
from app.ingestion import document_packages as p


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    selection = init.add_mutually_exclusive_group(required=True)
    selection.add_argument('--all', action='store_true')
    selection.add_argument('--source')
    for name in ('validate', 'revise', 'recover'):
        command = commands.add_parser(name)
        command.add_argument('--package', type=Path, required=True)
        if name == 'revise':
            command.add_argument('--supersedes-current', action='store_true', required=True)
            command.add_argument('--source', required=True, help='Path relative to app/assets.')
    for command in commands.choices.values():
        command.add_argument('--json', action='store_true')
    return cli


def main(argv=None):
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if (argv and argv[0] in ('approve', 'extract')) or any(
        arg.split('=')[0] in ('--review', '--reviewer', '--content-only') for arg in argv
    ):
        return p.report(REPOSITORY_ROOT, 'prepare-retired', [{
            'status': 'failed', 'code': 'preparation_command_retired',
            'message': 'Use init, validate --package, revise --package and publish_documents --package. See docs/offline-indexing.md.',
        }], as_json='--json' in argv)
    args = parser().parse_args(argv)
    results = []
    try:
        if args.command == 'init':
            for path in p.initialize(REPOSITORY_ROOT, args.source):
                results.append({'status': 'processed', 'code': 'package_initialized', 'package': str(path.relative_to(REPOSITORY_ROOT))})
            if not results:
                results.append({'status': 'skipped', 'code': 'already_initialized'})
        elif args.command == 'revise':
            path = p.revise(REPOSITORY_ROOT, args.package, args.source, supersedes_current=args.supersedes_current)
            results.append({'status': 'processed', 'code': 'revision_created', 'package': str(path.relative_to(REPOSITORY_ROOT))})
        elif args.command == 'recover':
            from app.ingestion.package_storage import recover
            backup = recover(REPOSITORY_ROOT, args.package)
            results.append({'status': 'processed', 'code': 'package_recovered', 'backup': str(backup)})
        else:
            _, payload, _ = p.load_package(REPOSITORY_ROOT, args.package)
            results.append({'status': 'processed', 'code': 'valid_package', 'chunk_count': len(payload['chunks'])})
    except p.Rejected as exc:
        results.append({'status': 'rejected', 'code': str(exc)})
    except CatalogError:
        results.append({'status': 'rejected', 'code': 'source_rejected'})
    except Exception:
        results.append({'status': 'failed', 'code': 'operation_failed'})
    return p.report(REPOSITORY_ROOT, 'prepare-' + args.command, results, as_json=args.json)


if __name__ == '__main__':
    raise SystemExit(main())
