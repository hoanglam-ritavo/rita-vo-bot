"""One-time, local asset folder migration. Preview is read-only and the default."""

import argparse
import copy
import json
import os
from pathlib import Path
import sys
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.asset_catalog import (  # noqa: E402
    CatalogError, atomic_write, compare, file_fingerprint, inventory,
    json_text, load_catalog, reject_links, safe_relative, validate_schema,
)


GUIDES = '05-huong-dan-su-dung'
DISPOSAL = '01-noi-quy-qui-dinh/quy-trinh-thanh-ly-tai-san'
RULES = [
    (f'{GUIDES}/e-office/guidelines', f'{GUIDES}/e-office', [
        'e-booking/rtv-um-o365-outlookmeetingroom-v1-0.pdf',
        'e-leave/rtv-um-o365-e-leave-v1-0.pdf',
        'e-postal/rtv-um-o365-e-postal-v1-0.pdf',
        'e-workdate/rtv-um-o365-e-workdate-v1-0.pdf',
    ]),
    (f'{GUIDES}/it-system/bravo/guideline', f'{GUIDES}/bravo', [
        'rtv-um-erp-bravologinusingvpn-v1-0.pdf',
    ]),
    ('06-tai-lieu-video-hdsd-workit/1-file-huong-dan', f'{GUIDES}/workit', [
        '1-workit-nhan-su-hdsd-v1.pdf', '2-workit-vanphongso-hdsd-v1.pdf',
        '3-1-workit-quy-trinh-so-hdsd-v1.pdf', '3-2-workit-quy-trinh-so-hdsd-v1.pdf',
        '4-workit-duyet-phieu-cap-quan-ly-hdsd-v1.pdf',
        '5-workit-mobile-app-duyet-phieu-theo-quy-trinh-hdsd-v1.pdf',
        'huong-dan-thuc-hien-ho-so-xin-nghi-viec-xin-nghi-thai-san-rita-vo.pdf',
    ]),
    (f'{DISPOSAL}/rtv-quy-trinh-thanh-ly-tai-san-ccdc-phe-lieu', DISPOSAL, [
        'mau-bien-ban-thong-ke-ts-ccdc-pl-can-thanh-ly-huybo.xlsx',
        'mau-bien-ban-xu-ly-ts-ccdc-pl-kiem-pxk.xlsx',
        'rtv-quy-trinh-thanh-ly-tai-san-ccdc-phe-lieu.pdf',
    ]),
]
MAPPING = {f'{old}/{name}': f'{new}/{name}' for old, new, names in RULES for name in names}
EMPTY_DIRS = {
    '02-chinh-sach-phuc-loi', f'{GUIDES}/device',
    '01-noi-quy-qui-dinh/noi-quy-lao-dong-quy-dinh-quy-trinh-hien-hanh/quy-dinh-hien-hanh',
}
SOURCE_DIRS = EMPTY_DIRS | {
    f'{GUIDES}/it-system', f'{GUIDES}/it-system/bravo',
    '06-tai-lieu-video-hdsd-workit',
} | {old for old, _, _ in RULES} | {
    f'{GUIDES}/e-office/guidelines/{group}'
    for group in ('e-booking', 'e-leave', 'e-postal', 'e-workdate')
}


def asset_path(assets, relative):
    if not safe_relative(relative):
        raise CatalogError('Unsafe manifest path.')
    target = assets / relative
    reject_links(target)
    if not target.resolve().is_relative_to(assets.resolve()):
        raise CatalogError('Path outside assets.')
    return target


def tree(assets):
    """Include hidden entries and files ignored by the regular catalog scan."""
    reject_links(assets)
    files, directories = {}, {}
    queue = [assets]
    while queue:
        parent = queue.pop()
        for path in parent.iterdir():
            reject_links(path)
            relative = path.relative_to(assets).as_posix()
            asset_path(assets, relative)
            if path.is_dir():
                directories[relative] = getattr(path.stat(), 'st_file_attributes', 0)
                queue.append(path)
            elif path.is_file():
                files[relative] = {**file_fingerprint(path),
                                   'attributes': getattr(path.stat(), 'st_file_attributes', 0)}
            else:
                raise CatalogError('Non-regular entry rejected.')
    return files, directories


def paths(root):
    return root / 'assets', root / 'data/catalog/documents.json', root / 'data/reports/asset-restructure'


def preview(root):
    assets, catalog_path, runs = paths(root)
    reject_links(runs)
    if runs.exists():
        for journal in runs.glob('*/journal.json'):
            reject_links(journal)
            if load_catalog(journal)['status'] not in {'applied', 'rolled-back'}:
                raise CatalogError('An unfinished run requires rollback before another apply.')
    catalog = load_catalog(catalog_path)
    if validate_schema(catalog):
        raise CatalogError('Catalog schema or metadata validation failed.')
    difference = compare(catalog, inventory(assets))
    if any(difference[key] for key in ('added', 'missing', 'changed')):
        raise CatalogError('Catalog does not match source inventory.')
    files, directories = tree(assets)
    folded = [path.casefold() for path in [*files, *directories]]
    if len(set(folded)) != len(folded):
        raise CatalogError('Case-insensitive path collision.')
    for path in [*files, *directories]:
        if any(path == branch or path.startswith(branch + '/') for branch in SOURCE_DIRS):
            if path not in MAPPING and path not in SOURCE_DIRS:
                raise CatalogError('Unexpected entry in a source branch; review required.')
    operations = []
    records = {r['source_path']: r for r in catalog['documents']}
    for old, new in MAPPING.items():
        if old in files:
            if new.casefold() in folded:
                raise CatalogError('Destination exists; overwrite refused.')
            for parent in Path(new).parents:
                if parent.as_posix().casefold() in {p.casefold() for p in files}:
                    raise CatalogError('Destination parent is a file.')
            operations.append({'old': old, 'new': new, **files[old],
                               'document_id': records[old]['document_id'],
                               'revision_id': records[old]['revision_id']})
        elif new not in files:
            raise CatalogError('Expected source or migrated destination missing.')
    # A mixed layout is only recoverable through its original run journal.
    if operations and len(operations) != len(MAPPING):
        raise CatalogError('Partial migration detected; use rollback for the original run.')
    remove = sorted(SOURCE_DIRS & directories.keys(), key=lambda p: (-p.count('/'), p))
    create = set()
    for op in operations:
        for parent in Path(op['new']).parents:
            relative = parent.as_posix()
            if relative != '.' and relative not in directories:
                create.add(relative)
    return {'operations': operations, 'remove_dirs': remove,
            'create_dirs': sorted(create, key=lambda p: (p.count('/'), p)),
            'files_before': files, 'dirs_before': directories}


def renamed_catalog(before, operations):
    result = copy.deepcopy(before)
    mapping = {op['old']: op['new'] for op in operations}
    for record in result['documents']:
        record['source_path'] = mapping.get(record['source_path'], record['source_path'])
    return result


def move(source, destination):
    reject_links(source)
    reject_links(destination)
    if destination.exists():
        raise CatalogError('Destination exists; overwrite refused.')
    if os.name == 'nt':
        # Windows rename fails if the destination exists, including a race.
        os.rename(source, destination)
    else:
        os.link(source, destination)
        source.unlink()


def save_journal(run, status, completed):
    atomic_write(run / 'journal.json', json_text({'status': status, 'completed': completed}))


def restore_directory_attributes(path, attributes):
    if os.name == 'nt':
        import ctypes
        if not ctypes.windll.kernel32.SetFileAttributesW(str(path), attributes):
            raise OSError('Cannot restore directory attributes.')


def rollback(root, run_id):
    if str(UUID(run_id)) != run_id:
        raise CatalogError('Invalid run ID.')
    assets, catalog_path, runs = paths(root)
    run = runs / run_id
    reject_links(run)
    manifest = load_catalog(run / 'manifest.json')
    journal = load_catalog(run / 'journal.json')
    reject_links(run / 'catalog.before.json')
    before_text = (run / 'catalog.before.json').read_text(encoding='utf-8')
    before = json.loads(before_text)
    expected_after = renamed_catalog(before, manifest['operations'])
    if load_catalog(catalog_path) not in (before, expected_after):
        raise CatalogError('Catalog edited since migration; rollback refused.')
    for op in manifest['operations']:
        if MAPPING.get(op['old']) != op['new']:
            raise CatalogError('Manifest does not match the approved mapping.')
    files, directories = tree(assets)
    # Check ALL files, not just the moved subset, before any rollback mutation.
    expected = copy.deepcopy(manifest['files_before'])
    moved = []
    for op in manifest['operations']:
        old, new = op['old'], op['new']
        if old in files and new in files:
            raise CatalogError('Both source and destination exist; rollback refused.')
        if new in files:
            expected[new] = expected.pop(old)
            moved.append(op)
    if files != expected:
        raise CatalogError('Source files changed since migration; rollback refused.')
    allowed_dirs = set(manifest['dirs_before']) | set(manifest['create_dirs'])
    if set(directories) - allowed_dirs:
        raise CatalogError('Unexpected directories; rollback refused.')
    if journal['status'] == 'rolled-back':
        return
    save_journal(run, 'rolling-back', 0)
    for relative in sorted(manifest['dirs_before'], key=lambda p: (p.count('/'), p)):
        directory = asset_path(assets, relative)
        directory.mkdir(exist_ok=True)
    for index, op in enumerate(reversed(moved), 1):
        move(asset_path(assets, op['new']), asset_path(assets, op['old']))
        save_journal(run, 'rolling-back', index)
    atomic_write(catalog_path, before_text)
    for relative in reversed(manifest['create_dirs']):
        directory = asset_path(assets, relative)
        if directory.exists():
            directory.rmdir()  # Never recursive; new content prevents deletion.
    for relative, attributes in manifest['dirs_before'].items():
        restore_directory_attributes(asset_path(assets, relative), attributes)
    if tree(assets) != (manifest['files_before'], manifest['dirs_before']):
        raise CatalogError('Rollback verification failed.')
    save_journal(run, 'rolled-back', len(moved))


def apply(root):
    plan = preview(root)
    if not plan['operations'] and not plan['remove_dirs']:
        return None
    assets, catalog_path, runs = paths(root)
    reject_links(catalog_path)
    before_text = catalog_path.read_text(encoding='utf-8')
    before = json.loads(before_text)
    after = renamed_catalog(before, plan['operations'])
    run_id = str(uuid4())
    run = runs / run_id
    run.mkdir(parents=True)
    atomic_write(run / 'catalog.before.json', before_text, create_only=True)
    atomic_write(run / 'manifest.json', json_text(plan), create_only=True)
    save_journal(run, 'applying', 0)
    try:
        if tree(assets) != (plan['files_before'], plan['dirs_before']):
            raise CatalogError('Sources changed after preflight.')
        for relative in plan['create_dirs']:
            asset_path(assets, relative).mkdir()
        for index, op in enumerate(plan['operations'], 1):
            move(asset_path(assets, op['old']), asset_path(assets, op['new']))
            save_journal(run, 'applying', index)
        for relative in plan['remove_dirs']:
            asset_path(assets, relative).rmdir()
        expected = copy.deepcopy(plan['files_before'])
        for op in plan['operations']:
            expected[op['new']] = expected.pop(op['old'])
        if tree(assets)[0] != expected:
            raise CatalogError('Source verification failed after moves.')
        if load_catalog(catalog_path) != before:
            raise CatalogError('Catalog changed during migration.')
        atomic_write(catalog_path, json_text(after))
        save_journal(run, 'applied', len(plan['operations']))
    except Exception:
        try:
            rollback(root, run_id)
        except Exception as recovery_error:
            raise CatalogError(f'Recovery incomplete; inspect run {run_id} and use rollback.') from recovery_error
        raise
    return run_id


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('preview', 'apply', 'rollback'), nargs='?', default='preview')
    parser.add_argument('--run-id')
    args = parser.parse_args(argv)
    if (args.mode == 'rollback') != bool(args.run_id):
        parser.error('--run-id is required only for rollback')
    root = Path(__file__).resolve().parents[1]
    try:
        if args.mode == 'preview':
            plan = preview(root)
            for op in plan['operations']:
                print(f'{op["old"]} -> {op["new"]}')
            print(f'Files={len(plan["operations"])} create_dirs={len(plan["create_dirs"])} remove_dirs={len(plan["remove_dirs"])}')
        elif args.mode == 'apply':
            run_id = apply(root)
            print(f'Applied and verified: {run_id}' if run_id else 'Already normalized; no changes.')
        else:
            rollback(root, args.run_id)
            print('Rollback verified.')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        message = str(exc) if isinstance(exc, CatalogError) else 'Filesystem or run-record error; inspect local state.'
        print(message, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
