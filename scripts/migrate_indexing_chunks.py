"""Migrate indexing_workspace to app/chunks; not an indexing_chunks relocation.

Stop all ingestion before --apply; no backup is retained.
"""
import argparse
import hashlib
from pathlib import Path
import sys
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.ingestion import document_packages as p
from app.catalog.inventory import reject_links
from app.catalog.storage import load_catalog
from app.paths import workspace

NAMESPACE = UUID('a6253e7c-dd93-47aa-82a4-b61c5850846d')


def tree(directory):
    reject_links(directory)
    result = {}
    if directory.exists():
        queue = [directory]
        while queue:
            parent = queue.pop()
            for path in sorted(parent.iterdir()):
                reject_links(path)
                if path.is_file():
                    with path.open('rb') as stream:
                        result[path.relative_to(directory).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
                elif path.is_dir():
                    queue.append(path)
                else:
                    raise ValueError('Nonregular entry')
    return result


def migrate(root, apply=False):
    root = p.io_path(Path(root).resolve())
    old = p.io_path(root / 'indexing_workspace')
    new = p.io_path(workspace(root))
    reject_links(old)
    reject_links(new)
    if not old.exists():
        return {'status': 'processed', 'code': 'already_migrated'}
    p.require(old.resolve().parent == root.resolve(), 'unsafe_old_root')
    p.require(new.resolve() == root.resolve() / 'app' / 'chunks', 'unsafe_new_root')
    p.require(set(x.name for x in old.iterdir()) <= {'README.md', 'catalog', 'cache', 'reports', 'archive', 'review', 'approved', 'published_artifacts'}, 'unknown_workspace_entry')
    journals = list(new.glob('review/*/*/migration-plan.json'))
    p.require(len(journals) <= 1, 'multiple_migration_plans')
    if journals:
        plan = p.decode(journals[0].read_bytes())
        verify_plan(new, old, plan)
        if apply:
            remove_old(old, plan['old_files'])
        return {'status': 'processed', 'code': 'migration_resumed' if apply else 'migration_preview'}
    before = tree(old)  # Verify readability and links for every file before any deletion.
    catalog = load_catalog(old / 'catalog/documents.json')
    records = catalog['documents']
    by_id = {r['revision_id']: p.validate_metadata(r) for r in records}
    p.require(len(by_id) == len(records), 'duplicate_revision')
    targets = []
    for record in records:
        package_id = str(uuid5(NAMESPACE, record['revision_id']))
        manifest = {'schema_version': 2, 'kind': 'review', 'state': 'pending',
                    'identity': {k: record[k] for k in p.IDENTITY_FIELDS}}
        files = {'metadata.json': p.encoded(record), 'manifest.json': p.encoded(manifest),
                 'migration.json': p.encoded({'origin': 'catalog', 'metadata_sha256': p.canonical_hash(record)})}
        targets.append((new / 'review' / record['revision_id'] / package_id, files))
    for kind in ('review', 'approved'):
        base = old / kind
        if not base.exists():
            continue
        consumed = set()
        for manifest_path in sorted(base.glob('*/*/manifest.json')):
            directory = manifest_path.parent
            files = {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
            p.require(all(path.is_file() for path in directory.iterdir()), 'unknown_package_directory')
            manifest = p.decode(files['manifest.json'])
            p.require(manifest.get('schema_version') == 1, 'unknown_old_schema')
            extraction = p.decode(files['extraction.json'])
            record = by_id[extraction['identity']['revision_id']]
            p.require(p.identity(record, extraction['identity']['extraction_id']) == extraction['identity'], 'old_identity_mismatch')
            p.require(all(name in files and p.sha(files[name]) == digest for name, digest in manifest['files'].items()), 'old_hash_mismatch')
            if kind == 'approved':
                p.require(manifest.get('kind') == 'approval', 'old_approval_kind')
                # Preserve all original bytes; the sidecar does not confer approval on metadata.
                legacy = dict(files)
                legacy['migration.json'] = p.encoded({'requires_reapproval': True, 'metadata': record,
                                                     'original_files': {k: p.sha(v) for k, v in files.items()}})
                targets.append((new / kind / directory.parent.name / directory.name, legacy))
                review_manifest = p.decode(files['review-manifest.json'])
                files = {k: v for k, v in files.items() if k in {'extraction.json', 'draft.initial.json', 'draft.json', review_manifest['source_file']}}
            else:
                review_manifest = manifest
            review_manifest = dict(review_manifest, schema_version=2, state='ready',
                                   identity={k: record[k] for k in p.IDENTITY_FIELDS})
            files['manifest.json'] = p.encoded(review_manifest)
            files['metadata.json'] = p.encoded(record)
            files['migration.json'] = p.encoded({'origin': kind, 'original_manifest_sha256': p.sha(manifest_path.read_bytes())})
            target_id = str(uuid5(NAMESPACE, kind + '/' + directory.parent.name + '/' + directory.name))
            targets.append((new / 'review' / record['revision_id'] / target_id, files))
            consumed.update(path.relative_to(base).as_posix() for path in directory.iterdir())
        p.require(set(tree(base)) == consumed, 'unrecognized_old_package_files')
    for target, files in targets:
        reject_links(target)
        if target.exists():
            p.require(tree(target) == {k: p.sha(v) for k, v in files.items()}, 'migration_destination_conflict')
    if not apply:
        return {'status': 'processed', 'code': 'migration_preview', 'records': len(records), 'packages': len(targets), 'files_to_remove': len(before)}
    for target, files in targets:
        if not target.exists():
            p.write_package(target.parent, target.name, files)
        p.require(tree(target) == {k: p.sha(v) for k, v in files.items()}, 'migration_verification_failed')
    (new / 'review').mkdir(exist_ok=True, parents=True)
    (new / 'approved').mkdir(exist_ok=True, parents=True)
    plan = {'old_files': before, 'targets': {target.relative_to(new).as_posix(): {k: p.sha(v) for k, v in files.items()}
                                              for target, files in targets}}
    p.require(bool(targets), 'empty_catalog_requires_manual_review')
    journal = targets[0][0] / 'migration-plan.json'
    journal.write_bytes(p.encoded(plan))
    verify_plan(new, old, plan)
    remove_old(old, before)
    return {'status': 'processed', 'code': 'migrated', 'records': len(records), 'packages': len(targets)}


def verify_plan(new, old, plan):
    for relative, hashes in plan['targets'].items():
        from app.catalog.inventory import safe_relative
        p.require(safe_relative(relative), 'unsafe_migration_target')
        actual = tree(new / relative)
        actual.pop('migration-plan.json', None)
        p.require(actual == hashes, 'migration_verification_failed')
    remaining = tree(old)
    p.require(all(plan['old_files'].get(name) == digest for name, digest in remaining.items()), 'workspace_changed_during_migration')


def remove_old(old, hashes):
    # All targets and remaining source bytes were checked immediately before deletion.
    for name in sorted(tree(old), key=lambda name: (name.startswith('catalog/'), name)):
        path = old / name
        reject_links(path)
        p.require(path.resolve().is_relative_to(old.resolve()), 'unsafe_delete')
        with path.open('rb') as stream:
            p.require(hashlib.file_digest(stream, 'sha256').hexdigest() == hashes[name], 'source_changed_before_delete')
        path.unlink()
    for directory in sorted((d for d in old.rglob('*') if d.is_dir()), key=lambda d: len(d.parts), reverse=True):
        reject_links(directory)
        directory.rmdir()
    old.rmdir()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        print(p.encoded(migrate(ROOT, args.apply)).decode(), end='')
        return 0
    except Exception as exc:
        print('Migration stopped without further deletion: ' + (str(exc) if isinstance(exc, p.Rejected) else type(exc).__name__))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
