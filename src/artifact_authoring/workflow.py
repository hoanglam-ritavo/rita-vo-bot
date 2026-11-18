"""Explicit offline import/build, retaining runtime identity and acceptance policy."""
import os
from pathlib import Path
import tempfile

from src.paths import REPOSITORY_ROOT
from .sources import (GROUPS, SourceError, assemble, checked, decode, encode,
                      identifier, recheck)


def validate_pair(raw, root):
    from src.answering.catalog import validate_catalog, sha_bytes
    from src.retrieval.semantics import validate_artifact
    from src.semantics.corpus import corpus_snapshot
    if len(raw[0]) > 8_000_000 or len(raw[1]) > 16_000_000:
        raise SourceError('answer_artifact_too_large')
    lexicon = validate_artifact(decode(raw[0]))
    validate_catalog(decode(raw[1]), lexicon, sha_bytes(raw[0]),
                     corpus_snapshot(Path(root) / 'chunks'), root=root)


def build(root=REPOSITORY_ROOT, *, check=False):
    root = Path(root).absolute()
    raw, observed = assemble(root)
    validate_pair(raw, root)
    outputs = [checked(root / 'artifacts' / group / spec[0]) for group, spec in GROUPS.items()]
    before = [path.read_bytes() if path.exists() else None for path in outputs]
    recheck(observed)
    changed = [i for i in range(2) if before[i] != raw[i]]
    if check:
        if changed:
            raise SourceError('generated_artifact_outdated')
        return {'status': 'current', 'changed_files': 0}
    if changed:
        temporary = checked(root / '.tmp')
        temporary.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='artifact-build-', dir=temporary) as stage:
            staged = []
            for i, value in enumerate(raw):
                path = Path(stage) / str(i)
                with path.open('wb') as stream:
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
                staged.append(path)
            recheck(observed)
            for path, previous in zip(outputs, before):
                if (path.read_bytes() if checked(path).exists() else None) != previous:
                    raise SourceError('generated_artifact_changed')
            for i in changed:
                os.replace(staged[i], outputs[i])
    return {'status': 'built', 'changed_files': len(changed)}


def import_pair(lexicon_path, catalog_path, output, root=REPOSITORY_ROOT):
    """Write only a new private tree, never promote candidates into current sources."""
    if '..' in Path(output).parts:
        raise SourceError('new_private_output_required')
    root, output = Path(root).absolute(), checked(output)
    private = checked(root / '.tmp')
    if ('..' in output.parts or not output.is_relative_to(private)
            or output == private or output.exists()):
        raise SourceError('new_private_output_required')
    paths = [checked(lexicon_path), checked(catalog_path)]
    raw = tuple(path.read_bytes() for path in paths)
    validate_pair(raw, root)
    payloads = {}
    for group, value in zip(GROUPS, raw):
        data = decode(value)
        collections = GROUPS[group][1]
        index = {'schema_version': 1, 'metadata': {key: val for key, val in data.items()
                 if key not in (*collections, 'lexicon_sha256')}}
        for collection in collections:
            records = {}
            for row in data[collection]:
                name = identifier(row['document_id'] if collection == 'coverage' else row['id'])
                ref = collection + '/' + name + '.json'
                if collection == 'coverage':
                    records.setdefault(ref, []).append(row)
                elif ref in records:
                    raise SourceError('duplicate_source_id')
                else:
                    records[ref] = row
            index[collection] = list(records)
            for ref, record in records.items():
                payloads[f'artifacts/{group}/source/{ref}'] = encode(record)
        payloads[f'artifacts/{group}/source/index.json'] = encode(index)
    if any(path.read_bytes() != value for path, value in zip(paths, raw)):
        raise SourceError('source_changed')
    output.mkdir(parents=True, exist_ok=False)
    for relative, value in payloads.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    assembled, observed = assemble(output)
    recheck(observed)
    expected = [decode(value) for value in raw]
    expected[1]['lexicon_sha256'] = decode(assembled[1])['lexicon_sha256']
    if any(decode(a) != b for a, b in zip(assembled, expected)):
        raise SourceError('import_roundtrip_mismatch')
    return {'status': 'imported', 'source_files': len(payloads)}
