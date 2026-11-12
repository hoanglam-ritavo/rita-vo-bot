"""Embed and publish approved JSON packages; never parse original documents."""
import argparse
from collections import Counter
from pathlib import Path
import re

from app.paths import REPOSITORY_ROOT, workspace
from app.ingestion import document_packages as packages
from app.catalog.inventory import CatalogError, reject_links


def model_commit(value):
    if not re.fullmatch('[0-9a-fA-F]{40}', value):
        raise argparse.ArgumentTypeError('Expected full 40-hex model commit.')
    return value.lower()


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    selection = cli.add_mutually_exclusive_group(required=True)
    selection.add_argument('--approved', type=Path, action='append')
    selection.add_argument('--all', action='store_true')
    cli.add_argument('--model-revision', type=model_commit, required=True)
    cli.add_argument('--model-path', type=Path, required=True)
    cli.add_argument('--update-metadata', action='store_true')
    cli.add_argument('--json', action='store_true')
    cli.add_argument('--dry-run', action='store_true')
    cli.add_argument('--force', action='store_true')
    args = cli.parse_args(argv)
    from app.database.runtime import run_async
    return run_async(run(REPOSITORY_ROOT, args))


async def run(root, args):
    from app.database import close_database
    from app.ingestion.approved_publication import publish
    from app.ingestion.publish import PublishRejected
    results, candidates = [], []
    try:
        if args.all:
            base = workspace(root) / 'approved'
            reject_links(base)
            paths = []
            if base.exists():
                for revision in sorted(base.iterdir()):
                    reject_links(revision)
                    if revision.is_dir() and not revision.name.startswith('.'):
                        for package in sorted(revision.iterdir()):
                            reject_links(package)
                            if package.is_dir() and not package.name.startswith('.'):
                                paths.append(package)
        else:
            paths = args.approved
        directory_counts = Counter(path.parent.name for path in paths) if args.all else {}
        for path in paths:
            if args.all and directory_counts[path.parent.name] > 1:
                results.append({'status': 'rejected', 'code': 'multiple_approvals_select_one'})
                continue
            try:
                record, _, _, _ = packages.load_approval(root, path)
                candidates.append((path, record['revision_id']))
            except (packages.Rejected, CatalogError, KeyError, TypeError, ValueError):
                results.append({'status': 'rejected', 'code': 'invalid_approval'})
            except Exception:
                results.append({'status': 'failed', 'code': 'approval_read_failed'})
        counts = Counter(revision for _, revision in candidates)
        # Topological ordering within the selected batch; external targets are checked in PostgreSQL.
        ordered = []
        remaining = list(candidates)
        while remaining:
            ids = {revision for _, revision in remaining}
            ready = [(path, revision) for path, revision in remaining
                     if not any(r['revision_id'] in ids for r in packages.load_approval(root, path)[0]['relations'])]
            if not ready:
                results.append({'status': 'rejected', 'code': 'cyclic_batch_relations'})
                break
            ordered.extend(ready)
            remaining = [item for item in remaining if item not in ready]
        for path, revision in ordered:
            if counts[revision] > 1:
                results.append({'status': 'rejected', 'code': 'multiple_approvals_select_one', 'revision_id': revision})
                continue
            try:
                result = await publish(root, path, args.model_revision, model_path=args.model_path, dry_run=args.dry_run, force=args.force, update_metadata=args.update_metadata)
                results.append(result)
            except packages.Rejected as exc:
                results.append({'status': 'rejected', 'code': str(exc), 'revision_id': revision})
            except (PublishRejected, CatalogError):
                results.append({'status': 'rejected', 'code': 'metadata_or_source_rejected', 'revision_id': revision})
            except Exception:
                results.append({'status': 'failed', 'code': 'publication_failed', 'revision_id': revision})
        if not paths:
            results.append({'status': 'rejected', 'code': 'no_approvals'})
    except Exception:
        results.append({'status': 'failed', 'code': 'batch_failed'})
    finally:
        try:
            await close_database()
        except Exception:
            results.append({'status': 'failed', 'code': 'database_shutdown_failed'})
    try:
        return packages.report(root, 'publish-documents', results, as_json=args.json)
    except Exception:
        print('Results could not be written to stdout.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
