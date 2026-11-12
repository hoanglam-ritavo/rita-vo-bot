"""Extract a catalog revision to a review package without database/model access."""
import json
from uuid import uuid4

from app.ingestion import document_packages as packages
from app.ingestion.chunking import chunk_spans
from app.ingestion.document_extractors import extract, config
from app.paths import workspace


def initial_chunks(segments):
    chunks = []
    for segment in segments:
        text = segment['text']
        # Preserve exact code whitespace; prose uses existing boundary-aware chunking.
        spans = [(i, min(i + 500, len(text))) for i in range(0, len(text), 500)] if segment['location'].get('code') else chunk_spans(text)
        for start, end in spans:
            piece = text[start:end]
            if not piece.strip():
                continue
            if segment['location']['kind'] in ('csv', 'xlsx'):
                # Repeat full row/column context even when a long cell spans chunks.
                # Cell values and headers remain untouched in the extraction snapshot.
                context = {'sheet': segment['location'].get('sheet'), 'row': segment['location']['row'],
                           'headers': segment['details']['headers']}
                piece = 'Table context: ' + json.dumps(context, ensure_ascii=False) + '\n' + piece
            chunks.append({'chunk_id': str(uuid4()), 'content': piece,
                           'source_refs': [{'segment_id': segment['segment_id'], 'start': start, 'end': end}],
                           'review_notes': ''})
    return chunks


def prepare(root, directory, settings=None):
    settings = config(settings or {})
    directory = packages.io_path(directory)
    packages.require(directory.resolve().is_relative_to(packages.io_path(workspace(root) / 'review').resolve()), 'review_outside_workspace')
    record = packages.metadata(directory)
    source = packages.source_for(root, record)
    source_bytes = source.read_bytes()
    packages.require(packages.sha(source_bytes) == record['sha256'], 'source_changed')
    extraction_id = str(uuid4())
    parent = workspace(root) / 'review' / record['revision_id']
    public_target = parent / extraction_id
    parent = packages.io_path(parent)
    # Parser input is a byte-for-byte snapshot. Failed staging is deliberately retained.
    from app.catalog.inventory import reject_links
    reject_links(parent)
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / ('.extracting-' + extraction_id)
    staging.mkdir()
    source_name = 'source' + record['extension']
    snapshot = staging / source_name
    snapshot.write_bytes(source_bytes)
    segments, warnings, metadata = extract(snapshot, settings)
    packages.require(packages.sha(snapshot.read_bytes()) == record['sha256'], 'snapshot_changed')
    current = packages.metadata(directory)
    packages.source_for(root, current)
    packages.require(current == record, 'catalog_changed')
    ident = packages.identity(record, extraction_id)
    extraction = {'schema_version': 1, 'status': 'complete', 'identity': ident,
                  'configuration': {'pipeline_version': 1, 'chunk_size': 500, 'chunk_overlap': 100, **settings},
                  'parser_metadata': metadata, 'segments': segments, 'warnings': warnings}
    draft = {'schema_version': 1, 'identity': ident, 'chunks': initial_chunks(segments),
             'excluded': [], 'warning_decisions': {w['warning_id']: '' for w in warnings}}
    files = {source_name: source_bytes, 'extraction.json': packages.encoded(extraction),
             'draft.initial.json': packages.encoded(draft)}
    manifest = {'schema_version': 2, 'kind': 'review', 'state': 'ready',
                'identity': {k: record[k] for k in packages.IDENTITY_FIELDS}, 'source_file': source_name,
                'files': {key: packages.sha(value) for key, value in files.items()}}
    for name, data in files.items():
        if name != source_name:
            (staging / name).write_bytes(data)
    (staging / 'draft.json').write_bytes(files['draft.initial.json'])
    (staging / 'metadata.json').write_bytes(packages.encoded(record))
    (staging / 'manifest.json').write_bytes(packages.encoded(manifest))
    target = parent / extraction_id
    packages.require(not target.exists(), 'package_exists')
    staging.rename(target)
    return public_target
