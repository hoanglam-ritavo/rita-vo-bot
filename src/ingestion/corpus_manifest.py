"""Release selection for current document packages."""

import re
from pathlib import Path
from tempfile import NamedTemporaryFile

from src.catalog.inventory import reject_links
from src.paths import workspace
from . import document_packages as packages
from .package_storage import lock


FIELDS = {'document_id', 'revision_id', 'package_sha256', 'chunk_count'}


def manifest_path(root):
    return packages.io_path(workspace(root) / packages.MANIFEST_NAME)


def validate(data):
    packages.require(isinstance(data, dict) and set(data) == {'schema_version', 'packages'}
                     and type(data['schema_version']) is int and data['schema_version'] == 1,
                     'invalid_manifest_schema')
    entries = data['packages']
    packages.require(isinstance(entries, list) and entries, 'empty_or_invalid_manifest')
    documents, revisions = set(), set()
    for entry in entries:
        packages.require(isinstance(entry, dict) and set(entry) == FIELDS, 'invalid_manifest_entry')
        document_id, revision_id = entry['document_id'], entry['revision_id']
        packages.uuid(document_id)
        packages.uuid(revision_id)
        packages.require(document_id not in documents and revision_id not in revisions,
                         'duplicate_manifest_entry')
        digest, count = entry['package_sha256'], entry['chunk_count']
        packages.require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest)
                         and type(count) is int and count > 0, 'invalid_manifest_entry')
        documents.add(document_id)
        revisions.add(revision_id)
    return sorted(entries, key=lambda entry: entry['document_id'])


def read(root):
    path = manifest_path(root)
    reject_links(path)
    packages.require(path.is_file(), 'manifest_missing_or_invalid')
    raw = path.read_bytes()
    return validate(packages.decode(raw)), packages.sha(raw)


def describe(record, payload, files):
    return {'document_id': record['document_id'], 'revision_id': record['revision_id'],
            'package_sha256': packages.files_hash(files), 'chunk_count': len(payload['chunks'])}


def freeze(root, selected=None, *, replace=False):
    path = manifest_path(root)
    reject_links(path)
    packages.require(replace or not path.exists(), 'manifest_exists_use_replace')
    paths = packages.package_paths(root) if selected is None else selected
    entries = [describe(*packages.load_package(root, package, eligible=True)) for package in paths]
    data = {'schema_version': 1, 'packages': entries}
    data['packages'] = validate(data)
    raw = packages.encoded(data)

    # Serialize the existence check and replacement with other manifest writers.
    # Package loads above take workspace locks of their own.
    with lock(root, 'workspace'):
        reject_links(path)
        packages.require(replace or not path.exists(), 'manifest_exists_use_replace')
        if path.exists():
            packages.require(path.is_file(), 'manifest_missing_or_invalid')
        directory = packages.io_path(root / '.tmp')
        reject_links(directory)
        stream = NamedTemporaryFile(dir=directory, prefix='corpus-manifest-', delete=False)
        staged = Path(stream.name)
        try:
            with stream:
                stream.write(raw)
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)
    return data['packages'], packages.sha(raw)
