"""Prepare and approve self-contained local chunk packages."""
import argparse
from pathlib import Path
from collections import Counter
from uuid import uuid4
from app.paths import REPOSITORY_ROOT, assets
from app.catalog.inventory import CatalogError, file_fingerprint, safe_relative
from app.ingestion import document_packages as p


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--all', action='store_true', required=True)
    extract = commands.add_parser('extract')
    selection = extract.add_mutually_exclusive_group(required=True)
    selection.add_argument('--review', type=Path)
    selection.add_argument('--all', action='store_true')
    extract.add_argument('--config', type=Path)
    for name in ('validate', 'approve', 'revise'):
        command = commands.add_parser(name)
        command.add_argument('--review', type=Path, required=True)
        if name == 'approve':
            command.add_argument('--reviewer', required=True)
            command.add_argument('--content-only', action='store_true',
                                 help='Seal reviewed content with unverified metadata; not eligible for publication.')
        if name == 'revise':
            command.add_argument('--source', required=True, help='Path relative to app/assets; creates a new revision of the same document.')
    for command in commands.choices.values():
        command.add_argument('--json', action='store_true')
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    root = REPOSITORY_ROOT
    results = []
    try:
        if args.command == 'init':
            for path in p.initialize(root):
                results.append({'status': 'processed', 'code': 'pending_created', 'package': str(path.relative_to(root))})
        elif args.command == 'revise':
            record = p.metadata(args.review)
            p.require(safe_relative(args.source), 'unsafe_source')
            record.update(file_fingerprint(assets(root) / args.source))
            record.update(source_path=args.source, revision_id=str(uuid4()), indexing_decision='hold',
                          review_status='pending', lifecycle_status='unknown', relations=[], review_notes=[])
            path = p.pending(root, record)
            results.append({'status': 'processed', 'code': 'revision_created', 'package': str(path.relative_to(root))})
        elif args.command == 'extract':
            from app.ingestion.document_preparation import prepare
            from app.ingestion.document_extractors import config
            settings = config(p.decode(args.config.read_bytes()) if args.config else {})
            paths = p.review_paths(root) if args.all else [args.review]
            if args.all:
                ready_ids = {p.metadata(path)['revision_id'] for path in paths if p.decode(p.read_file(path, 'manifest.json')).get('state') == 'ready'}
                paths = [path for path in paths if p.metadata(path)['revision_id'] not in ready_ids and p.decode(p.read_file(path, 'manifest.json')).get('state') == 'pending']
            counts = Counter(p.metadata(path)['revision_id'] for path in paths)
            for path in paths:
                try:
                    record = p.metadata(path)
                    if args.all and (record['extension'] not in p.SUPPORTED or record['indexing_decision'] == 'exclude'
                                     or record['lifecycle_status'] not in ('active', 'unknown')):
                        results.append({'status': 'skipped', 'code': 'unsupported_or_ineligible'})
                        continue
                    p.require(counts[record['revision_id']] == 1, 'multiple_reviews_select_one')
                    target = prepare(root, path, settings)
                    results.append({'status': 'processed', 'code': 'review_created', 'package': str(target.relative_to(root))})
                except (p.Rejected, CatalogError) as exc:
                    results.append({'status': 'rejected', 'code': str(exc) if isinstance(exc, p.Rejected) else 'source_rejected'})
                except Exception:
                    results.append({'status': 'failed', 'code': 'extraction_failed'})
        elif args.command == 'validate':
            extraction, _, chunks, _ = p.load_review(args.review)
            p.source_for(root, p.metadata(args.review))
            results.append({'status': 'processed', 'code': 'valid_review', 'chunk_count': len(chunks)})
        else:
            path = p.approve(root, args.review, args.reviewer, content_only=args.content_only)
            results.append({'status': 'processed', 'code': 'approved', 'package': str(path.relative_to(root))})
    except p.Rejected as exc:
        results.append({'status': 'rejected', 'code': str(exc)})
    except CatalogError:
        results.append({'status': 'rejected', 'code': 'source_rejected'})
    except Exception:
        results.append({'status': 'failed', 'code': 'operation_failed'})
    return p.report(root, 'prepare-' + args.command, results, as_json=args.json)


if __name__ == '__main__':
    raise SystemExit(main())
