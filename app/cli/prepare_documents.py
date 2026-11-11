"""Prepare local documents, validate edited JSON, and seal approval snapshots."""
import argparse
from pathlib import Path
from uuid import UUID

from app.paths import REPOSITORY_ROOT
from app.catalog.inventory import CatalogError
from app.ingestion import document_packages as packages


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='command', required=True)
    extract = commands.add_parser('extract')
    selection = extract.add_mutually_exclusive_group(required=True)
    selection.add_argument('--revision-id', type=UUID)
    selection.add_argument('--all', action='store_true')
    extract.add_argument('--config', type=Path, help='UTF-8 extraction settings JSON; relative paths use current working directory.')
    for name in ('validate', 'approve'):
        command = commands.add_parser(name)
        command.add_argument('--review', type=Path, required=True)
        if name == 'approve':
            command.add_argument('--reviewer', required=True)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    root = REPOSITORY_ROOT
    results = []
    try:
        if args.command == 'extract':
            from app.ingestion.document_preparation import prepare
            from app.ingestion.document_extractors import config
            settings = config(packages.decode(args.config.read_bytes()) if args.config else {})
            records = packages.catalog(root)
            if not args.all:
                records = [r for r in records if r['revision_id'] == str(args.revision_id)]
                packages.require(bool(records), 'revision_missing')
            for record in records:
                result = {'revision_id': record['revision_id']}
                if args.all and (record['extension'] not in packages.SUPPORTED or record['indexing_decision'] == 'exclude'
                                 or record['lifecycle_status'] not in ('active', 'unknown')):
                    results.append(result | {'status': 'skipped', 'code': 'unsupported_or_ineligible'})
                    continue
                try:
                    path = prepare(root, record['revision_id'], settings)
                    results.append(result | {'status': 'processed', 'code': 'review_created', 'package': str(path.relative_to(root))})
                except packages.Rejected as exc:
                    results.append(result | {'status': 'rejected', 'code': str(exc)})
                except CatalogError:
                    results.append(result | {'status': 'rejected', 'code': 'catalog_rejected'})
                except Exception:
                    results.append(result | {'status': 'failed', 'code': 'extraction_failed'})
        elif args.command == 'validate':
            extraction, _, chunks, _ = packages.load_review(args.review)
            record, _ = packages.selected(root, extraction['identity']['revision_id'])
            packages.require(packages.identity(record, extraction['identity']['extraction_id']) == extraction['identity'], 'catalog_identity_mismatch')
            results.append({'status': 'processed', 'code': 'valid_review', 'chunk_count': len(chunks), 'revision_id': record['revision_id']})
        else:
            path = packages.approve(root, args.review, args.reviewer)
            results.append({'status': 'processed', 'code': 'approved', 'package': str(path.relative_to(root))})
    except packages.Rejected as exc:
        results.append({'status': 'rejected', 'code': str(exc)})
    except CatalogError:
        results.append({'status': 'rejected', 'code': 'catalog_rejected'})
    except Exception:
        results.append({'status': 'failed', 'code': 'operation_failed'})
    try:
        return packages.report(root, 'prepare-' + args.command, results)
    except Exception:
        print('Report could not be written; inspect workspace access.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
