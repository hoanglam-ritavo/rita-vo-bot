"""Explicit v2 -> v3 scaffold conversion; agent authors semantics afterwards."""
import argparse
from uuid import uuid4
from pathlib import Path
from src.paths import REPOSITORY_ROOT
from src.ingestion import document_packages as p
from src.ingestion.package_storage import replace, lock


def convert(root, directory, apply=False):
    record, _, original = p.load_package(root, directory)
    if record['schema_version'] == 3:
        return {'code': 'already_v3'}
    files = dict(original)
    for name in ('metadata.json', 'extraction.json', 'chunks.json'):
        data = p.decode(files[name])
        data['schema_version'] = 3
        if name == 'chunks.json':
            for c in data['chunks']:
                c.update(id=str(uuid4()), role='detail')
            data.update(scopes=[], items=[], values=[])
        files[name] = p.encoded(data)
    p.validate_chunks(p.decode(files['extraction.json']), p.decode(files['chunks.json']))
    if apply:
        with lock(root, 'workspace'), lock(root, record['document_id']):
            replace(root, record['document_id'], files,
                    before_finalize=lambda: p.require(p._snapshot_files(directory) == original, 'package_changed'))
    return {'code': 'converted_scaffold' if apply else 'preview_scaffold',
            'document_id': record['document_id'], 'semantic_authoring_required': True}


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--package', required=True, type=Path)
    cli.add_argument('--apply', action='store_true')
    args = cli.parse_args(argv)
    try:
        print(convert(REPOSITORY_ROOT, args.package, args.apply))
        return 0
    except Exception:
        print('Structured conversion failed; package recovery may be required. No source content logged.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
