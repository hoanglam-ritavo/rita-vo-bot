"""Publish validated v2/v3 packages with atomic metadata, vectors and evidence."""
import copy
from decimal import Decimal
import re
from uuid import UUID, uuid4
from datetime import datetime, timezone
from sqlalchemy import select, delete, text, or_
from src.database import session_scope
from src.database.models import Document, DocumentRevision, DocumentAccessRule, DocumentRelation, IngestionRun, DocumentChunk
from src.catalog.importer import revision_values, IMPORT_LOCK
from src.ingestion import document_packages as packages
from src.ingestion.embeddings import MODEL_NAME, embed, validate_vectors, model_fingerprint

def configuration(payload, model_revision):
    packages.require(isinstance(model_revision, str) and re.fullmatch('[0-9a-f]{40}', model_revision), 'model_commit_required')
    content = copy.deepcopy(payload['chunks'])
    evidence = {'chunks': content, 'excluded': payload['excluded'],
                'warning_decisions': payload['warning_decisions']}
    if 'structured' in payload:
        evidence['structured'] = payload['structured']
    return {'pipeline': 'agent-json-v2', 'source_sha256': payload['identity']['sha256'],
            'content_hash': packages.canonical_hash(evidence), 'model_name': MODEL_NAME,
            'model_revision': model_revision, 'embedding_dimensions': 384,
            'normalize_embeddings': True, 'prefix': 'passage: ', 'batch_size': 16}


async def snapshot(db, record, update_metadata=False):
    revision_id = UUID(record['revision_id'])
    revision = await db.get(DocumentRevision, revision_id)
    values = revision_values(record)
    groups = set(await db.scalars(select(DocumentAccessRule.group_name).where(DocumentAccessRule.revision_id == revision_id)))
    relations = set((await db.execute(select(DocumentRelation.target_revision_id, DocumentRelation.relation_type)
                   .where(DocumentRelation.source_revision_id == revision_id))).all())
    desired_relations = {(UUID(r['revision_id']), r['type']) for r in record['relations']}
    resolved = set()
    missing = []
    target_state = []
    for target, kind in sorted(desired_relations, key=lambda item: (str(item[0]), item[1])):
        other = await db.get(DocumentRevision, target)
        target_state.append((str(target), kind, str(other.document_id) if other else None, other.sha256 if other else None))
        if other is None and kind == 'supersedes':
            missing.append(str(target))
            continue
        packages.require(other is not None, 'relation_target_missing')
        resolved.add((target, kind))
        if kind == 'supersedes':
            packages.require(other.document_id == UUID(record['document_id']), 'supersedes_document_mismatch')
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
        differs = (any(getattr(revision, k) != v for k, v in values.items()) or groups != set(record['access_groups']) or relations != resolved)
        packages.require(not differs or update_metadata, 'metadata_conflict_use_update_metadata')
    active = await db.scalar(select(IngestionRun).where(IngestionRun.revision_id == revision_id, IngestionRun.is_active.is_(True)))
    # Include metadata in the concurrency token even when an explicit update is allowed.
    token = (tuple((k, str(getattr(revision, k))) for k in sorted(values)) if revision else None,
             tuple(sorted(groups)), tuple(sorted((str(t), k) for t, k in relations)), str(active.id) if active else None, str(collision.id) if collision else None, tuple(target_state))
    resolved_record = dict(record, relations=[{'revision_id': str(t), 'type': k}
                                             for t, k in sorted(resolved, key=lambda item: (str(item[0]), item[1]))])
    return revision, active, token, resolved_record, missing


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
    record, payload, files = packages.load_package(root, directory, eligible=True)
    config = configuration(payload, model_revision)
    config.update(model_files_sha256=model_fingerprint(model_path),
                  package_sha256=packages.files_hash(files),
                  metadata_sha256=packages.sha(files['metadata.json']))
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        revision, active, expected, resolved_record, missing = await snapshot(db, record, update_metadata)
        declared = sorted({r['revision_id'] for r in record['relations'] if r['type'] == 'supersedes'})
        if declared:
            config.update(declared_supersedes=declared, missing_supersedes=missing)
        if not force and active is not None and active.configuration == config:
            return {'status': 'skipped', 'code': 'identical_active_run', 'run_id': str(active.id), 'missing_supersedes': missing}
    if dry_run:
        return {'status': 'processed', 'code': 'dry_run', 'revision_id': record['revision_id'], 'missing_supersedes': missing}
    chunks = embed(model_path, copy.deepcopy(payload['chunks']), model_revision)
    validate_vectors(chunks)
    packages.require([{k: v for k, v in c.items() if k != 'embedding'} for c in chunks] == payload['chunks'], 'embedding_changed_content')
    packages.require(model_fingerprint(model_path) == config['model_files_sha256'], 'model_changed')
    packages.require(packages.snapshot_files(directory) == files, 'package_changed')
    run_id = uuid4()
    now = datetime.now(timezone.utc)
    async with session_scope() as db:
        await db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': IMPORT_LOCK})
        revision, active, actual, resolved_record, _ = await snapshot(db, record, update_metadata)
        packages.require(packages.snapshot_files(directory) == files, 'package_changed')
        packages.require(actual == expected, 'concurrent_publication_or_metadata_change')
        await write_metadata(db, resolved_record, revision)
        if active:
            active.is_active = False
            await db.flush()
        db.add(IngestionRun(id=run_id, revision_id=UUID(record['revision_id']), status='succeeded', is_active=True,
            started_at=now, finished_at=datetime.now(timezone.utc), source_sha256=record['sha256'], configuration=config,
            model_name=MODEL_NAME, model_revision=model_revision, embedding_dimensions=384, chunk_count=len(chunks)))
        await db.flush()
        db.add_all(DocumentChunk(run_id=run_id, chunk_index=c['chunk_index'], content=c['content'], source_location=c['source_location'],
            extraction_method=c['extraction_method'], embedding=c['embedding'],
            author_id=c.get('author_id'), role=c.get('role')) for c in chunks)
        if 'structured' in payload:
            from src.database.models import EvidenceScope, EvidenceItem, EvidenceValue
            data = payload['structured']
            db.add_all(EvidenceScope(run_id=run_id, author_id=r['id'], data=r) for r in data['scopes'])
            db.add_all(EvidenceItem(run_id=run_id, author_id=r['id'], data=r) for r in data['items'])
            await db.flush()
            db.add_all(EvidenceValue(run_id=run_id, author_id=r['id'], item_id=r['item_id'],
                numeric_value=Decimal(r['value']) if r['type'] == 'decimal' else None, data=r) for r in data['values'])
    return {'status': 'processed', 'code': 'published', 'run_id': str(run_id), 'revision_id': record['revision_id'], 'missing_supersedes': missing}
