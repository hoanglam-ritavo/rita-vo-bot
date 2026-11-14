"""Embed and publish agent-authored v2 JSON packages; never parse original documents."""
import argparse
from collections import Counter
from pathlib import Path

from src.paths import REPOSITORY_ROOT
from src.ingestion import document_packages as packages
from src.catalog.inventory import CatalogError
from src.ingestion.model_identity import model_commit


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    selection = cli.add_mutually_exclusive_group(required=True)
    selection.add_argument('--package', type=Path, action='append')
    selection.add_argument('--all', action='store_true')
    cli.add_argument('--model-revision', type=model_commit, required=True)
    cli.add_argument('--model-path', type=Path, required=True)
    cli.add_argument('--update-metadata', action='store_true')
    cli.add_argument('--json', action='store_true')
    cli.add_argument('--dry-run', action='store_true')
    cli.add_argument('--force', action='store_true')
    import sys
    raw = list(sys.argv[1:] if argv is None else argv)
    if any(a.split('=')[0] == '--approved' for a in raw):
        return packages.report(REPOSITORY_ROOT, 'publish-retired', [{'status': 'failed', 'code': 'approved_flag_retired', 'message': 'Convert the selected legacy package, then use --package. See docs/offline-indexing.md.'}], as_json='--json' in raw)
    args = cli.parse_args(raw)
    from src.database.runtime import run_async
    return run_async(run(REPOSITORY_ROOT, args))


async def run(root, args):
    from src.database import close_database
    from src.ingestion.approved_publication import publish
    from src.ingestion.publish import PublishRejected
    results, candidates = [], []
    records = {}
    try:
        if args.all:
            paths = packages.package_paths(root)
        else:
            paths = args.package
        for path in paths:
            try:
                record, _, _ = packages.load_package(root, path, eligible=True)
                candidates.append((path, record['revision_id']))
                records[path] = record
            except packages.Rejected as exc:
                results.append({'status': 'rejected', 'code': str(exc), 'package': str(path)})
            except (CatalogError, KeyError, TypeError, ValueError):
                results.append({'status': 'rejected', 'code': 'invalid_package'})
            except Exception:
                results.append({'status': 'failed', 'code': 'package_read_failed'})
        counts = Counter(revision for _, revision in candidates)
        # Topological ordering within the selected batch; external targets are checked in PostgreSQL.
        ordered = []
        remaining = list(candidates)
        while remaining:
            ids = {revision for _, revision in remaining}
            ready = [(path, revision) for path, revision in remaining
                     if not any(r['revision_id'] in ids for r in records[path]['relations'])]
            if not ready:
                results.append({'status': 'rejected', 'code': 'cyclic_batch_relations'})
                break
            ordered.extend(ready)
            remaining = [item for item in remaining if item not in ready]
        for path, revision in ordered:
            if counts[revision] > 1:
                results.append({'status': 'rejected', 'code': 'multiple_packages_select_one', 'revision_id': revision})
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
            results.append({'status': 'rejected', 'code': 'no_packages'})
    except packages.Rejected as exc:
        results.append({'status': 'rejected', 'code': str(exc)})
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
