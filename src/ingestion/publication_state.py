"""Read-only publication comparison and bounded embedding reuse.

Byte hashes remain provenance, not a test of runtime-content equality.
Legacy agent-json-v2 runs used the same exact-text passage preprocessing.
"""
import copy
import math

from sqlalchemy import select

from src.database.models import DocumentChunk, EvidenceScope, EvidenceItem, EvidenceValue
from src.ingestion import document_packages as packages

EMBEDDING_KEYS = ('model_name', 'model_revision', 'model_files_sha256',
                  'embedding_dimensions', 'normalize_embeddings', 'prefix', 'batch_size')
PREPROCESSING = 'exact-text-passage-v1'


def compatible(run, config):
    if run is None or run.status != 'succeeded':
        return False
    old = run.configuration
    return (old.get('pipeline') == config['pipeline'] == 'agent-json-v2'
            and old.get('fingerprint_version', 1) == 1
            and old.get('embedding_preprocessing', PREPROCESSING) == PREPROCESSING
            and all(old.get(k) == config[k] for k in EMBEDDING_KEYS)
            and all(getattr(run, k) == config[k] for k in
                    ('model_name', 'model_revision', 'embedding_dimensions')))


def vector_values(vector):
    if vector is None:
        return None
    try:
        values = list(vector)
        if len(values) != 384 or any(isinstance(v, bool) or not math.isfinite(float(v)) for v in values):
            return None
        return [float(v) for v in values]
    except (ValueError, TypeError, OverflowError):
        return None


async def read_storage(db, run):
    """Detached values also form the optimistic concurrency token for stored data."""
    if run is None:
        return {'chunks': [], 'structured': {}}
    rows = list(await db.scalars(select(DocumentChunk).where(
        DocumentChunk.run_id == run.id).order_by(DocumentChunk.chunk_index)))
    chunks = []
    for row in rows:
        chunk = {k: copy.deepcopy(getattr(row, k)) for k in
                 ('chunk_index', 'content', 'source_location', 'extraction_method')}
        if row.author_id is not None:
            chunk.update(author_id=row.author_id, role=row.role)
        elif row.role is not None:
            chunk['role'] = row.role
        chunk['embedding'] = vector_values(row.embedding)
        chunks.append(chunk)
    structured = {}
    for key, model in [('scopes', EvidenceScope), ('items', EvidenceItem), ('values', EvidenceValue)]:
        records = list(await db.scalars(select(model).where(model.run_id == run.id)))
        structured[key] = sorted((copy.deepcopy(r.data) for r in records), key=lambda r: r['id'])
    return {'chunks': chunks, 'structured': structured}


def storage_matches(run, storage, payload, config):
    if not compatible(run, config) or run.chunk_count != len(payload['chunks']):
        return False
    if run.configuration.get('source_sha256') != config['source_sha256'] or run.source_sha256 != config['source_sha256']:
        return False
    # Includes exclusions/warning decisions that are retained only in configuration.
    if run.configuration.get('content_hash') != config['content_hash']:
        return False
    chunks = storage['chunks']
    if any(c['embedding'] is None for c in chunks):
        return False
    if [{k: v for k, v in c.items() if k != 'embedding'} for c in chunks] != payload['chunks']:
        return False
    desired = payload.get('structured', {})
    return all(storage['structured'].get(k, []) == sorted(desired.get(k, []), key=lambda r: r['id'])
               for k in ('scopes', 'items', 'values'))


def plan_vectors(payload, run, storage, config, force=False):
    available = {}
    if not force and compatible(run, config):
        for chunk in storage['chunks']:
            if chunk['embedding'] is not None:
                available.setdefault(chunk['content'], chunk['embedding'])
    chunks = copy.deepcopy(payload['chunks'])
    missing = {}
    reused = 0
    for chunk in chunks:
        if chunk['content'] in available:
            chunk['embedding'] = list(available[chunk['content']])
            reused += 1
        else:
            missing.setdefault(chunk['content'], dict(chunk, chunk_index=len(missing)))
    if force:
        return chunks, copy.deepcopy(payload['chunks']), 0
    return chunks, list(missing.values()), reused


def storage_token(run, storage):
    return packages.canonical_hash({'run': str(run.id) if run else None,
                                   'configuration': run.configuration if run else None,
                                   'state': {k: getattr(run, k) for k in
                                             ('status', 'chunk_count', 'source_sha256', 'model_name',
                                              'model_revision', 'embedding_dimensions')} if run else None,
                                   'chunks': storage['chunks'], 'structured': storage['structured']})
