"""Explicit, non-destructive v1 review/approval to v2 package conversion."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ingestion import document_packages as p
from app.ingestion import legacy_packages as legacy
from app.catalog.inventory import reject_links, CatalogError
from app.paths import assets, workspace


def equivalent(left, right):
    if left.keys() != right.keys():
        return False
    return all(p.decode(left[name]) == p.decode(right[name]) if name.endswith('.json')
               else left[name] == right[name] for name in left)


def convert(root, directory, *, apply=False):
    root, directory = Path(root), Path(directory)
    directory = p.io_path(directory)
    # Only explicitly selected packages in the current legacy workspace are supported.
    base = p.io_path(workspace(root)).resolve()
    reject_links(directory)
    p.require(directory.resolve().is_relative_to(base), 'legacy_outside_workspace')
    relative = directory.resolve().relative_to(base)
    p.require(len(relative.parts) == 3 and relative.parts[0] in ('review', 'approved'), 'legacy_location')
    for part in relative.parts[1:]:
        p.uuid(part)
    original = p.snapshot_files(directory)
    manifest = p.decode(p.read_file(directory, 'manifest.json'))
    extraction = draft = None
    if relative.parts[0] == 'approved':
        record, _, _, approved_files = legacy.load_approval(root, directory)
        p.require(set(original) == set(approved_files), 'legacy_files')
        extraction = p.decode(original['extraction.json'])
        draft = p.decode(original['draft.json'])
    else:
        record = legacy.metadata(directory)
        p.require(record['revision_id'] == directory.parent.name, 'legacy_location')
        if manifest.get('state') == 'ready':
            extraction, draft, _, _ = legacy.load_review(directory, allow_unresolved=True)
            p.require(set(original) == {'metadata.json', 'manifest.json', 'extraction.json',
                       'draft.initial.json', 'draft.json', 'source' + record['extension']}, 'legacy_files')
        else:
            p.require(manifest.get('state') == 'pending', 'legacy_state')
            p.require(set(original) <= {'metadata.json', 'manifest.json', 'source' + record['extension']}, 'legacy_files')
    converted = dict(record)
    converted.pop('review_status')
    converted['evidence_notes'] = converted.pop('review_notes')
    converted['schema_version'] = 2
    p.validate_metadata(converted)
    files = {'metadata.json': p.encoded(converted)}
    source_name = 'source' + record['extension']
    source = None
    if source_name in original:
        data = original[source_name]
    else:
        source = assets(root) / record['source_path']
        reject_links(source)
        data = source.read_bytes() if source.exists() else None
    if data is not None:
        p.require(p.sha(data) == record['sha256'] and len(data) == record['size_bytes'], 'snapshot_mismatch')
        files[source_name] = data
    if extraction is not None:
        extraction = {k: v for k, v in extraction.items() if k != 'identity'}
        extraction['schema_version'] = 2
        chunks = {'schema_version': 2,
                  'chunks': [{'content': c['content'], 'source_refs': c['source_refs'],
                              'notes': c['review_notes']} for c in draft['chunks']],
                  'excluded': draft['excluded'], 'warning_decisions': draft['warning_decisions']}
        p.validate_chunks(extraction, chunks, allow_unresolved=True)
        files.update({'extraction.json': p.encoded(extraction), 'chunks.json': p.encoded(chunks)})
    target = workspace(root) / record['document_id'] / record['revision_id']
    reject_links(target)

    def unchanged():
        p.require(p.snapshot_files(directory) == original, 'legacy_package_changed')
        if source is not None and data is not None:
            reject_links(source)
            p.require(source.read_bytes() == data, 'source_changed')

    unchanged()
    if p.io_path(target).exists():
        p.require(equivalent(p.snapshot_files(target), files), 'conversion_target_conflict')
        return {'status': 'skipped', 'code': 'already_converted', 'package': str(target.relative_to(root))}
    if apply:
        p.write_package(target.parent, target.name, files, before_finalize=unchanged)
    return {'status': 'processed', 'code': 'package_converted' if apply else 'conversion_preview',
            'package': str(target.relative_to(root)), 'complete': extraction is not None}


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--package', type=Path, required=True)
    cli.add_argument('--apply', action='store_true')
    cli.add_argument('--json', action='store_true')
    args = cli.parse_args(argv)
    try:
        result = convert(ROOT, args.package, apply=args.apply)
    except p.Rejected as exc:
        result = {'status': 'rejected', 'code': str(exc)}
    except (CatalogError, KeyError, TypeError, ValueError):
        result = {'status': 'rejected', 'code': 'invalid_legacy_package'}
    except Exception:
        result = {'status': 'failed', 'code': 'conversion_failed'}
    return p.report(ROOT, 'convert-package', [result], as_json=args.json)


if __name__ == '__main__':
    raise SystemExit(main())
