"""Preview or migrate explicitly selected v2 revision directories to current packages."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingestion import document_packages as p
from app.ingestion.package_storage import lock, available, replace, workspace_available


def inspect(document):
    """Read every old revision before backing up the entire document directory."""
    result = {}
    p.reject_links(document)
    for revision in sorted(document.iterdir()):
        p.uuid(revision.name)
        files = p.snapshot_files(revision)
        record = p.validate_metadata(p.decode(files.get('metadata.json', b'{}')))
        p.require(record['document_id'] == document.name and record['revision_id'] == revision.name,
                  'metadata_identity_changed')
        source = 'source' + record['extension']
        p.require(set(files) <= {'metadata.json', 'extraction.json', 'chunks.json', source}, 'package_files')
        p.require(source in files and p.sha(files[source]) == record['sha256']
                  and len(files[source]) == record['size_bytes'], 'snapshot_mismatch')
        if 'extraction.json' in files and 'chunks.json' in files:
            p.validate_chunks(p.decode(files['extraction.json']), p.decode(files['chunks.json']), allow_unresolved=True)
        result[revision.name] = files
    p.require(bool(result), 'no_revision_packages')
    return result


def convert(root, document, *, revision_id=None, apply=False):
    root = Path(root)
    document = p.package_directory(root, document)
    workspace_available(root)
    available(root, document.name)
    if (document / 'metadata.json').exists():
        p.metadata(document)
        return {'status': 'skipped', 'code': 'already_current'}
    originals = inspect(document)
    p.require(revision_id is not None or len(originals) == 1, 'select_revision_explicitly')
    chosen = revision_id if revision_id is not None else next(iter(originals))
    p.require(chosen in originals, 'selected_revision_missing')
    files = originals[chosen]
    record = p.decode(files['metadata.json'])
    def unique_source():
        # Reject a duplicate source in other current or old document directories.
        for other in document.parent.iterdir():
            if other == document or other.name.startswith('.') or other.name in ('review', 'approved'):
                continue
            p.uuid(other.name)
            records = ([p.metadata(other)] if (other / 'metadata.json').exists() else
                       [p.decode(f['metadata.json']) for f in inspect(other).values()])
            p.require(all(r['source_path'].casefold() != record['source_path'].casefold() for r in records),
                      'duplicate_source_package')

    unique_source()
    if apply:
        with lock(root, 'workspace'), lock(root, document.name):
            workspace_available(root)
            unique_source()
            def unchanged():
                p.require(inspect(document) == originals, 'package_changed')
            replace(root, document.name, files, before_finalize=unchanged)
    return {'status': 'processed', 'code': 'layout_migrated' if apply else 'layout_preview',
            'document_id': document.name, 'revision_id': chosen, 'revisions_backed_up': len(originals)}


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--document', type=Path, required=True)
    cli.add_argument('--revision-id')
    cli.add_argument('--apply', action='store_true')
    cli.add_argument('--json', action='store_true')
    args = cli.parse_args(argv)
    try:
        result = convert(ROOT, args.document, revision_id=args.revision_id, apply=args.apply)
    except p.Rejected as exc:
        result = {'status': 'rejected', 'code': str(exc)}
    except Exception:
        result = {'status': 'failed', 'code': 'layout_migration_failed'}
    return p.report(ROOT, 'migrate-layout', [result], as_json=args.json)


if __name__ == '__main__':
    raise SystemExit(main())
