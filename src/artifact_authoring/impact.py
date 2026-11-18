"""Dependency-local source reconciliation. Never queries or publishes a database."""
from collections import Counter
from pathlib import Path
import os
import tempfile

from src.paths import REPOSITORY_ROOT
from src.answering.catalog import METADATA, validate_catalog, sha_bytes
from src.retrieval.semantics import validate_artifact
from src.semantics.corpus import corpus_snapshot, decode_json
from .sources import assemble, checked, encode, recheck, SourceError


def inspect(root=REPOSITORY_ROOT):
    root = Path(root).absolute()
    raw, observed = assemble(root)
    catalog = decode_json(raw[1])
    validate_catalog(catalog, validate_artifact(decode_json(raw[0])), sha_bytes(raw[0]))
    snapshot = corpus_snapshot(root / 'chunks')
    packages = {p['document_id']: p for p in snapshot['packages']}
    metadata = {doc: decode_json((root / 'chunks' / doc / 'metadata.json').read_bytes()) for doc in packages}
    chunks = {}
    for chunk in snapshot['chunks']:
        chunks.setdefault(chunk['document_id'], []).append(chunk)
    rows, updates = [], {}
    for card in catalog['cards']:
        for aspect, section in card['sections'].items():
            for unit in section['units']:
                for index, ref in enumerate(unit['refs']):
                    doc = ref['document_id']
                    package, meta = packages.get(doc), metadata.get(doc, {})
                    if package is None:
                        state, candidates = 'source_missing', []
                    elif (package['revision_id'] != ref['revision_id']
                          or meta.get('sha256') != ref['source_sha256']
                          or any(meta.get(key) != ref['metadata'][key] for key in METADATA)):
                        state, candidates = 'context_changed', []
                    else:
                        candidates = [c for c in chunks[doc] if c['content_sha256'] == ref['content_sha256']
                                      and ref['quote'] in c['content']]
                        state = ('unchanged' if candidates[0]['chunk_index'] == ref['chunk_index'] else 'relinkable') \
                            if len(candidates) == 1 else 'ambiguous' if candidates else 'content_changed'
                    address = (card['id'], aspect, unit['id'], index)
                    if state == 'relinkable':
                        updates[address] = candidates[0]['chunk_index']
                    rows.append({'task_id': card['id'], 'unit_id': unit['id'], 'aspect': aspect,
                                 'reference': index, 'state': state})
    recheck(observed)
    return {'schema_version': 1, 'package_fingerprint': snapshot['package_fingerprint'],
            'summary': dict(Counter(row['state'] for row in rows)), 'references': rows}, updates, snapshot, observed


def refresh(root=REPOSITORY_ROOT):
    """Explicit write command: update only unique byte-identical evidence links.

    Other references remain stale and are refused at runtime. Coverage records
    new chunks as unavailable until authored; no approval or completeness inferred.
    """
    root = Path(root).absolute()
    report, updates, snapshot, observed = inspect(root)
    changed = {}
    index_path = root / 'artifacts/answers/source/index.json'
    index = decode_json(observed[index_path])
    used = set()
    for relative in index['cards']:
        path = index_path.parent / relative
        card = decode_json(observed[path])
        for aspect, section in card['sections'].items():
            for unit in section['units']:
                for n, ref in enumerate(unit['refs']):
                    key = (card['id'], aspect, unit['id'], n)
                    if key in updates:
                        ref['chunk_index'] = updates[key]
                    used.add((ref['document_id'], ref['chunk_index']))
        value = encode(card)
        if value != observed[path]:
            changed[path] = value
    grouped = {}
    for chunk in snapshot['chunks']:
        key = (chunk['document_id'], chunk['chunk_index'])
        disposition = 'template' if key in used else 'unavailable'
        row = {'document_id': key[0], 'chunk_index': key[1], 'disposition': disposition,
               'note': 'Linked task evidence; current content must pass runtime validation.' if key in used
               else 'No maintained task binding; explicit document lookup only. Review required.'}
        grouped.setdefault(key[0], []).append(row)
    index['coverage'] = ['coverage/' + doc + '.json' for doc in sorted(grouped)]
    index['metadata']['package_fingerprint'] = snapshot['package_fingerprint']
    changed[index_path] = encode(index)
    for doc, rows in grouped.items():
        changed[index_path.parent / 'coverage' / (doc + '.json')] = encode(rows)
    lexical_index = root / 'artifacts/semantics/source/index.json'
    lexical = decode_json(observed[lexical_index])
    lexical['metadata']['package_fingerprint'] = snapshot['package_fingerprint']
    changed[lexical_index] = encode(lexical)
    recheck(observed)
    if corpus_snapshot(root / 'chunks')['package_fingerprint'] != snapshot['package_fingerprint']:
        raise SourceError('packages_changed')
    # Stage all bytes before replacing individual source files. Runtime outputs
    # remain untouched until the separate build/check step.
    stage_root = checked(root / '.tmp')
    stage_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='evidence-refresh-', dir=stage_root) as temporary:
        staged = []
        for n, (path, value) in enumerate(changed.items()):
            checked(path)
            staged_path = Path(temporary) / str(n)
            staged_path.write_bytes(value)
            staged.append((path, staged_path))
        recheck(observed)
        for path, staged_path in staged:
            path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_path, path)
        # Remove only formerly declared coverage files for removed packages.
        for relative in decode_json(observed[index_path])['coverage']:
            path = checked(index_path.parent / relative)
            if path not in changed:
                path.unlink()
    return report
