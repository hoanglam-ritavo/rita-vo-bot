"""Publish sealed packages using local models and atomic per-revision metadata."""
import copy
import math
import re
from pathlib import Path
from uuid import UUID, uuid4
from datetime import datetime, timezone
from sqlalchemy import select, delete, text, or_
from app.database import session_scope
from app.database.models import Document, DocumentRevision, DocumentAccessRule, DocumentRelation, IngestionRun, DocumentChunk
from app.catalog.importer import revision_values, IMPORT_LOCK
from app.catalog.inventory import reject_links, file_fingerprint
from app.ingestion import document_packages as packages
from app.ingestion.embeddings import MODEL_NAME

def configuration(payload, model_revision):
    packages.require(isinstance(model_revision, str) and re.fullmatch('[0-9a-f]{40}', model_revision), 'model_commit_required')
    # Review/extraction IDs and approval timestamps are audit identity, not content identity.
    content = copy.deepcopy(payload['chunks'])
    for chunk in content:
        chunk['source_location'].pop('review_chunk_id', None)
    evidence = {'chunks': content, 'excluded': payload['excluded'],
                'warning_decisions': payload['warning_decisions']}
    return {'pipeline': 'approved-json-v1', 'source_sha256': payload['identity']['sha256'],
            'content_hash': packages.canonical_hash(evidence), 'model_name': MODEL_NAME,
            'model_revision': model_revision, 'embedding_dimensions': 384,
            'normalize_embeddings': True, 'prefix': 'passage: ', 'batch_size': 16}


def embed(model_path, chunks, model_revision):
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(str(model_path), local_files_only=True, trust_remote_code=False)
    packages.require(model.get_sentence_embedding_dimension() == 384, 'model_dimensions')
    texts = ['passage: ' + c['content'] for c in chunks]
    # Do not rely on encode's truncation. Include all special tokens in the count.
    limit = min(model.max_seq_length, model.tokenizer.model_max_length)
    packages.require(type(limit) is int and limit > 0, 'invalid_model_token_limit')
    for content in texts:
        tokens = model.tokenizer(content, add_special_tokens=True, truncation=False)['input_ids']
        packages.require(len(tokens) <= limit, 'chunk_exceeds_model_limit_reapprove')
    vectors = model.encode(texts, batch_size=16, normalize_embeddings=True,
                           convert_to_numpy=True, show_progress_bar=False)
    packages.require(len(vectors) == len(chunks), 'embedding_count')
    for chunk, vector in zip(chunks, vectors):
        chunk['embedding'] = vector.tolist()
    return chunks


def validate_vectors(chunks):
    packages.require(bool(chunks), 'empty_chunks')
    for index, chunk in enumerate(chunks):
        vector = chunk.get('embedding')
        packages.require(chunk['chunk_index'] == index and isinstance(vector, list) and len(vector) == 384
                         and all(type(v) in (int, float) and math.isfinite(v) for v in vector), 'invalid_vectors')


def model_fingerprint(path):
    path = Path(path)
    reject_links(path)
    packages.require(path.is_dir(), 'local_model_missing')
    files = {}
    queue = [path]
    while queue:
        for item in sorted(queue.pop().iterdir()):
            reject_links(item)
            if item.is_dir():
                queue.append(item)
            elif item.is_file():
                files[item.relative_to(path).as_posix()] = file_fingerprint(item)['sha256']
            else:
                raise packages.Rejected('nonregular_model_file')
    packages.require(bool(files), 'local_model_empty')
    return packages.canonical_hash(files)


async def snapshot(db, record, update_metadata=False):
    revision_id = UUID(record['revision_id'])
    revision = await db.get(DocumentRevision, revision_id)
    values = revision_values(record)
    groups = set(await db.scalars(select(DocumentAccessRule.group_name).where(DocumentAccessRule.revision_id == revision_id)))
    relations = set((await db.execute(select(DocumentRelation.target_revision_id, DocumentRelation.relation_type)
                   .where(DocumentRelation.source_revision_id == revision_id))).all())
    desired_relations = {(UUID(r['revision_id']), r['type']) for r in record['relations']}
    for target, kind in desired_relations:
        other = await db.get(DocumentRevision, target)
        packages.require(other is not None, 'relation_target_missing')
        if kind == 'duplicate_of':
            packages.require(other.sha256 == record['sha256'], 'duplicate_hash_mismatch')
    edges = list((await db.execute(select(DocumentRelation.source_revision_id, DocumentRelation.target_revision_id)
                       .where(DocumentRelation.relation_type == 'supersedes'))).all())
    graph = {}
    for source, target in edges:
        if source != revision_id:
            graph.setdefault(source, []).append(target)
    graph[revision_id] = [target for target, kind in desired_relations if kind == 'supersedes']
    pending, seen = list(graph[revision_id]), set()
    while pending:
        target = pending.pop()
        packages.require(target != revision_id, 'supersedes_cycle')
        if target not in seen:
            seen.add(target)
            pending.extend(graph.get(target, []))
    collision = await db.scalar(select(DocumentRevision).where(
        or_(DocumentRevision.source_path_key == values['source_path_key'], DocumentRevision.document_id == values['document_id']), DocumentRevision.is_catalog_current.is_(True),
        DocumentRevision.id != revision_id))
    if collision is not None:
        packages.require(collision.document_id == values['document_id'] and
                         (collision.id, 'supersedes') in desired_relations, 'current_revision_requires_explicit_supersedes')
    if revision:
        packages.require(all(getattr(revision, k) == values[k] for k in ('document_id', 'sha256', 'size_bytes', 'extension')), 'revision_identity_conflict')
        differs = (any(getattr(revision, k) != v for k, v in values.items()) or groups != set(record['access_groups']) or relations != desired_relations)
        packages.require(not differs or update_metadata, 'metadata_conflict_use_update_metadata')
    active = await db.scalar(select(IngestionRun).where(IngestionRun.revision_id == revision_id, IngestionRun.is_active.is_(True)))
    # Include metadata in the concurrency token even when an explicit update is allowed.
    token = (tuple((k, str(getattr(revision, k))) for k in sorted(values)) if revision else None,
             tuple(sorted(groups)), tuple(sorted((str(t), k) for t, k in relations)), str(active.id) if active else None, str(collision.id) if collision else None)
    return revision, active, token


async def write_metadata(db, record, revision):
    document_id, revision_id = UUID(record['document_id']), UUID(record['revision_id'])
    if await db.get(Document, document_id) is None:
        db.add(Document(id=document_id))
        await db.flush()
    old = await db.scalar(select(DocumentRevision).where(DocumentRevision.document_id == document_id,
                          DocumentRevision.is_catalog_current.is_(True), DocumentRevision.id != revision_id))
    if old:
        old.is_catalog_current = False
        old.lifecycle_status = 'superseded'
        await db.flush()
    if revision is None:
        db.add(DocumentRevision(id=revision_id, **revision_values(record)))
    else:
        for key, value in revision_values(record).items():
            setattr(revision, key, value)
    await db.flush()
    await db.execute(delete(DocumentAccessRule).where(DocumentAccessRule.revision_id == revision_id))
    await db.execute(delete(DocumentRelation).where(DocumentRelation.source_revision_id == revision_id))
    db.add_all(DocumentAccessRule(revision_id=revision_id, group_name=g) for g in set(record['access_groups']))
    db.add_all(DocumentRelation(source_revision_id=revision_id, target_revision_id=UUID(r['revision_id']), relation_type=r['type'])
               for r in record['relations'])


async def publish(root, directory, model_revision, *, model_path, dry_run=False, force=False, update_metadata=False):
    record, payload, approval, files = packages.load_approval(root, directory)
    packages.require(approval.get('approval_scope', 'publication') != 'content',
                     'metadata_not_approved_for_publication')
    config = configuration(payload, model_revision)
    config.update(pipeline='approved-json-v2', model_files_sha256=model_fingerprint(model_path),
                  approval_id=approval['approval_id'], approval_manifest_sha256=packages.sha(files['manifest.json']),
                  metadata_sha256=packages.sha(files['metadata.json']))
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        revision, active, expected = await snapshot(db, record, update_metadata)
        if not force and active is not None and active.configuration == config:
            return {'status': 'skipped', 'code': 'identical_active_run', 'run_id': str(active.id)}
    if dry_run:
        return {'status': 'processed', 'code': 'dry_run', 'revision_id': record['revision_id']}
    chunks = embed(model_path, copy.deepcopy(payload['chunks']), model_revision)
    validate_vectors(chunks)
    packages.require([{k: v for k, v in c.items() if k != 'embedding'} for c in chunks] == payload['chunks'], 'embedding_changed_content')
    packages.require(model_fingerprint(model_path) == config['model_files_sha256'], 'model_changed')
    packages.require(packages.load_approval(root, directory)[3] == files, 'approval_changed')
    run_id = uuid4()
    now = datetime.now(timezone.utc)
    async with session_scope() as db:
        await db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': IMPORT_LOCK})
        revision, active, actual = await snapshot(db, record, update_metadata)
        packages.require(actual == expected, 'concurrent_publication_or_metadata_change')
        await write_metadata(db, record, revision)
        if active:
            active.is_active = False
            await db.flush()
        db.add(IngestionRun(id=run_id, revision_id=UUID(record['revision_id']), status='succeeded', is_active=True,
            started_at=now, finished_at=datetime.now(timezone.utc), source_sha256=record['sha256'], configuration=config,
            model_name=MODEL_NAME, model_revision=model_revision, embedding_dimensions=384, chunk_count=len(chunks)))
        await db.flush()
        db.add_all(DocumentChunk(run_id=run_id, chunk_index=c['chunk_index'], content=c['content'], source_location=c['source_location'],
            extraction_method=c['extraction_method'], embedding=c['embedding']) for c in chunks)
    return {'status': 'processed', 'code': 'published', 'run_id': str(run_id), 'revision_id': record['revision_id']}
