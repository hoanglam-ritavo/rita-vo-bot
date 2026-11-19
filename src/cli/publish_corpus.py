"""Freeze, plan or apply the repository's corpus release manifest."""

import argparse
import json
import re
from pathlib import Path

from src.catalog.inventory import CatalogError
from src.ingestion import corpus_manifest, corpus_publication, document_packages as packages
from src.ingestion.model_identity import model_commit
from src.paths import REPOSITORY_ROOT


def manifest_sha(value):
    if not re.fullmatch('[a-f0-9]{64}', value):
        raise argparse.ArgumentTypeError('Expected a lowercase SHA-256 digest.')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    freeze = commands.add_parser('freeze', help='Write chunks/manifest.json from validated packages.')
    selection = freeze.add_mutually_exclusive_group(required=True)
    selection.add_argument('--all', action='store_true')
    selection.add_argument('--package', action='append', type=Path)
    freeze.add_argument('--replace', action='store_true', help='Allow replacing the existing manifest.')
    for name, help_text in (
        ('plan', 'Check inputs and dependency order without accessing PostgreSQL.'),
        ('apply', 'Check inputs and publish to the configured PostgreSQL database.'),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument('--model-path', type=Path, required=True)
        command.add_argument('--model-revision', type=model_commit, required=True)
        command.add_argument('--expected-manifest-sha256', type=manifest_sha,
                             help='Require this manifest digest before checking or publishing packages.')
    return run(REPOSITORY_ROOT, parser.parse_args(argv))


def run(root, args):
    report = {'operation': args.operation, 'manifest_path': 'chunks/manifest.json'}
    try:
        if args.operation == 'freeze':
            entries, fingerprint = corpus_manifest.freeze(root, args.package, replace=args.replace)
            report.update(manifest_sha256=fingerprint, package_count=len(entries),
                          chunk_count=sum(entry['chunk_count'] for entry in entries))
            status, code = 'completed', 'manifest_frozen'
        else:
            report.update(phase='preflight', preflight=[], waves=[], results=[], unattempted=[],
                          database_checks='not_run')
            waves, selected = corpus_publication.prepare(
                root, args.model_path, args.model_revision, report,
                expected_manifest=getattr(args, 'expected_manifest_sha256', None))
            if args.operation == 'apply':
                from src.database.runtime import run_async

                status, code = run_async(corpus_publication.apply(
                    root, args.model_path, args.model_revision, waves, selected, report))
            else:
                status, code = 'planned', 'database_checks_deferred'
    except packages.Rejected as exc:
        status, code = 'rejected', str(exc)
    except CatalogError:
        status, code = 'rejected', 'corpus_source_rejected'
    except Exception:
        status, code = 'failed', 'corpus_operation_failed'
    print(json.dumps(dict(report, status=status, code=code), ensure_ascii=True))
    return {'rejected': 1, 'failed': 2}.get(status, 0)


if __name__ == '__main__':
    raise SystemExit(main())
