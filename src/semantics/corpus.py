"""Read-only package inventory and byte identity, distinct from DB corpus identity."""
import hashlib
import json
from pathlib import Path

from src.paths import REPOSITORY_ROOT


class SemanticPreparationError(ValueError):
    """Content-safe preparation failure code."""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def decode_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SemanticPreparationError('duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(SemanticPreparationError('nonfinite_json')))
    except (UnicodeError, json.JSONDecodeError):
        raise SemanticPreparationError('invalid_semantic_json') from None


def read_json(path):
    return decode_json(_bytes(Path(path)))


def _bytes(path):
    if path.is_symlink() or path.is_junction():
        raise SemanticPreparationError('linked_package_path')
    try:
        return path.read_bytes()
    except OSError:
        raise SemanticPreparationError('package_file_unavailable') from None


def corpus_snapshot(root=None):
    """Hash every package JSON/source snapshot; never infer active DB publications.

    The verbose chunks/excerpts are private authoring material. Public command
    output must use counts/hashes only. Re-read byte identities before returning
    to reject concurrent editing, rather than publishing a mixed snapshot.
    """
    root = Path(root) if root is not None else REPOSITORY_ROOT / 'chunks'
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise SemanticPreparationError('invalid_chunks_root')
    packages, chunks, observed = [], [], []
    directories = sorted(root.iterdir(), key=lambda p: p.name)
    for directory in directories:
        if not directory.is_dir():
            continue
        if directory.is_symlink() or directory.is_junction():
            raise SemanticPreparationError('linked_package_path')
        if not (directory / 'metadata.json').exists():
            raise SemanticPreparationError('incomplete_package')
        metadata = read_json(directory / 'metadata.json')
        if not isinstance(metadata, dict):
            raise SemanticPreparationError('invalid_package_identity')
        extension = metadata.get('extension')
        if not isinstance(extension, str) or extension not in {'.pdf', '.txt', '.md', '.csv', '.xlsx'}:
            raise SemanticPreparationError('invalid_source_extension')
        names = ['metadata.json', 'extraction.json', 'chunks.json', 'source' + extension]
        files = {name: _bytes(directory / name) for name in names}
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        # Decode the captured bytes, not potentially newer versions read from disk.
        try:
            metadata = decode_json(files['metadata.json'])
            extraction = decode_json(files['extraction.json'])
            content = decode_json(files['chunks.json'])
        except (ValueError, UnicodeError):
            raise SemanticPreparationError('invalid_package_json') from None
        if any(not isinstance(value, dict) for value in (metadata, extraction, content)):
            raise SemanticPreparationError('invalid_package_identity')
        document_id = metadata.get('document_id')
        if document_id != directory.name or type(content.get('schema_version')) is not int or content['schema_version'] not in (2, 3):
            raise SemanticPreparationError('invalid_package_identity')
        if any(value.get('schema_version') != content['schema_version'] for value in (metadata, extraction)):
            raise SemanticPreparationError('package_schema_mismatch')
        source = files['source' + extension]
        if hashes['source' + extension] != metadata.get('sha256') or len(source) != metadata.get('size_bytes'):
            raise SemanticPreparationError('source_snapshot_mismatch')
        try:
            source_segments = extraction['segments']
            if not isinstance(source_segments, list) or any(not isinstance(segment, dict)
                    or not isinstance(segment.get('segment_id'), str) for segment in source_segments):
                raise ValueError()
            segments = {segment['segment_id']: segment for segment in source_segments}
            if len(segments) != len(source_segments):
                raise ValueError()
            package_chunks = content['chunks']
            if not isinstance(package_chunks, list) or not package_chunks:
                raise ValueError()
            for index, chunk in enumerate(package_chunks):
                text, refs = chunk['content'], chunk['source_refs']
                if not isinstance(text, str) or not text.strip() or not isinstance(refs, list) or not refs:
                    raise ValueError()
                excerpts = []
                for ref in refs:
                    segment = segments[ref['segment_id']]
                    start, end = ref['start'], ref['end']
                    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(segment['text']):
                        raise ValueError()
                    excerpts.append({'source_ref': ref, 'text': segment['text'][start:end],
                                     'location': segment['location']})
                chunks.append({'document_id': document_id, 'chunk_index': index,
                    'content_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    'content': text, 'source_refs': refs, 'source_excerpts': excerpts,
                    'author_id': chunk.get('id'), 'role': chunk.get('role')})
        except (KeyError, TypeError, ValueError):
            raise SemanticPreparationError('invalid_chunk_provenance') from None
        packages.append({'document_id': document_id, 'revision_id': metadata.get('revision_id'),
            'title': metadata.get('title'), 'source_file': 'source' + extension,
            'hashes': hashes, 'chunk_count': len(package_chunks)})
        observed.extend((directory / name, sha) for name, sha in hashes.items())
    if not packages:
        raise SemanticPreparationError('empty_corpus')
    if sorted(p.name for p in root.iterdir() if p.is_dir()) != sorted(p.name for p in directories if p.is_dir()):
        raise SemanticPreparationError('packages_changed')
    if any(hashlib.sha256(_bytes(path)).hexdigest() != sha for path, sha in observed):
        raise SemanticPreparationError('packages_changed')
    identity = [{key: package[key] for key in ('document_id', 'revision_id', 'hashes')} for package in packages]
    return {'schema_version': 1, 'package_fingerprint': digest(identity), 'packages': packages,
            'chunks': chunks, 'package_count': len(packages), 'chunk_count': len(chunks)}


def snapshot_fingerprint(root=None):
    return corpus_snapshot(root)['package_fingerprint']
