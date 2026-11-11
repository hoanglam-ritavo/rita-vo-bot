"""One-time, verified relocation of local artifacts; never touches source assets."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.catalog.inventory import CatalogError, reject_links
if os.name == 'nt':
    ROOT = Path('\\\\?\\' + str(ROOT))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    source = ROOT / 'app/data'
    target = ROOT / 'indexing_workspace'
    reject_links(source)
    reject_links(target)
    report_dir = target / 'archive/pre-reorganization/relocation' / str(uuid4())
    report_dir.mkdir(parents=True, exist_ok=False)
    report = {'files': [], 'blocked': [], 'directories': []}
    mapping = {'catalog': 'catalog', 'cache': 'cache',
               'extracted': 'archive/pre-reorganization/extracted',
               'reports': 'archive/pre-reorganization/reports',
               'README.md': 'archive/pre-reorganization/original-README.md'}

    def record_error(path, exc):
        report['blocked'].append({'source': str(path.relative_to(ROOT)), 'error': type(exc).__name__})

    def inspect(path, destination):
        try:
            reject_links(path)
            reject_links(destination)
            if not destination.resolve().is_relative_to(target.resolve()):
                raise ValueError('Destination outside workspace')
            if path.is_dir():
                report['directories'].append(str(path.relative_to(ROOT)))
                for child in path.iterdir():
                    inspect(child, destination / child.name)
            else:
                if destination.exists():
                    raise FileExistsError('Destination exists')
                report['files'].append({'source': str(path.relative_to(ROOT)),
                    'destination': str(destination.relative_to(ROOT)),
                    'size': path.stat().st_size, 'sha256': digest(path), 'status': 'pending'})
        except (OSError, ValueError, CatalogError) as exc:
            record_error(path, exc)

    for child in source.iterdir():
        if child.name not in mapping:
            record_error(child, ValueError('Unmapped source'))
        else:
            inspect(child, target / mapping[child.name])

    def save(name):
        staged = report_dir / (name + '.tmp')
        staged.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        staged.replace(report_dir / name)

    save('before.json')  # Durable inventory precedes all copies/removals.
    for relative in report['directories']:
        old_relative = (ROOT / relative).relative_to(source)
        destination = target / mapping[old_relative.parts[0]] / Path(*old_relative.parts[1:])
        try:
            reject_links(destination)
            destination.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError, CatalogError) as exc:
            record_error(ROOT / relative, exc)
    for item in report['files']:
        incoming, outgoing = ROOT / item['source'], ROOT / item['destination']
        try:
            reject_links(incoming)
            reject_links(outgoing)
            if not incoming.resolve().is_relative_to(source.resolve()):
                raise ValueError('Source outside data directory')
            outgoing.parent.mkdir(parents=True, exist_ok=True)
            with incoming.open('rb') as src, outgoing.open('xb') as dst:
                shutil.copyfileobj(src, dst)
            if (outgoing.stat().st_size != item['size'] or digest(outgoing) != item['sha256']
                    or digest(incoming) != item['sha256']):
                raise ValueError('Hash mismatch')
            item['status'] = 'verified_copy'
            with (report_dir / 'progress.jsonl').open('a', encoding='utf-8') as journal:
                journal.write(json.dumps(item) + '\n')
            incoming.unlink()
            item['status'] = 'moved'
        except (OSError, ValueError, CatalogError) as exc:
            record_error(incoming, exc)
        with (report_dir / 'progress.jsonl').open('a', encoding='utf-8') as journal:
            journal.write(json.dumps(item) + '\n')

    # Only empty, checked source directories can be removed. No recursive delete.
    for relative in sorted(report['directories'], key=len, reverse=True):
        directory = ROOT / relative
        try:
            reject_links(directory)
            if directory.resolve().is_relative_to(source.resolve()):
                directory.rmdir()
        except OSError:
            pass
    if not report['blocked']:
        source.rmdir()
    save('result.json')
    moved = sum(item['status'] == 'moved' for item in report['files'])
    print(f'Files inventoried={len(report["files"])}; verified/moved={moved}; blocked={len(report["blocked"])}')
    print(f'Relocation record: {report_dir.relative_to(ROOT)}')
    return 1 if report['blocked'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
