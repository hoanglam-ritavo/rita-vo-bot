"""Publish validated v2/v3 packages with atomic metadata, vectors and evidence."""
import copy
import asyncio
import re
from uuid import UUID, uuid4
from sqlalchemy import select, delete, text, or_
from src.database import session_scope
from src.database.models import Document, DocumentRevision, DocumentAccessRule, DocumentRelation, IngestionRun, DocumentChunk
from src.catalog.importer import revision_values, IMPORT_LOCK
from src.ingestion import document_packages as packages
from src.ingestion.embeddings import MODEL_NAME, embed, validate_vectors, model_fingerprint
from src.ingestion import attempts
from src.ingestion.audit import metadata_snapshot, record_change
from src.ingestion.evidence import write_evidence
from src.ingestion.publication_state import (PREPROCESSING, read_storage, storage_matches,
                                            storage_token, plan_vectors, compatible)

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
    attempt_id = db.info.get('publication_attempt_id')
    before = await metadata_snapshot(db, revision)
    if old:
        old_before = await metadata_snapshot(db, old)
        old.is_catalog_current = False
        old.lifecycle_status = 'superseded'
        await db.flush()
        await record_change(db, old, old_before, attempt_id, 'superseded')
    if revision is None:
        revision = DocumentRevision(id=revision_id, **revision_values(record))
        db.add(revision)
    else:
        for key, value in revision_values(record).items():
            setattr(revision, key, value)
    await db.flush()
    await db.execute(delete(DocumentAccessRule).where(DocumentAccessRule.revision_id == revision_id))
    await db.execute(delete(DocumentRelation).where(DocumentRelation.source_revision_id == revision_id))
    db.add_all(DocumentAccessRule(revision_id=revision_id, group_name=g) for g in set(record['access_groups']))
    db.add_all(DocumentRelation(source_revision_id=revision_id, target_revision_id=UUID(r['revision_id']), relation_type=r['type'])
               for r in record['relations'])
    await db.flush()
    await record_change(db, revision, before, attempt_id, 'created' if before is None else 'updated')


def metadata_matches(revision, record, token):
    if revision is None:
        return False
    return (all(getattr(revision, k) == v for k, v in revision_values(record).items())
            and set(token[1]) == set(record['access_groups'])
            and set(token[2]) == {(r['revision_id'], r['type']) for r in record['relations']})


async def reuse_source(db, active, record):
    if active is not None:
        return active
    # Only the current, explicitly superseded revision of this same document.
    targets = [UUID(r['revision_id']) for r in record['relations'] if r['type'] == 'supersedes']
    if not targets:
        return None
    return await db.scalar(select(IngestionRun).join(DocumentRevision,
        IngestionRun.revision_id == DocumentRevision.id).where(
            DocumentRevision.document_id == UUID(record['document_id']),
            DocumentRevision.id.in_(targets), DocumentRevision.is_catalog_current.is_(True),
            IngestionRun.is_active.is_(True), IngestionRun.status == 'succeeded'))


async def publish(root, directory, model_revision, *, model_path, dry_run=False, force=False,
                  update_metadata=False, expected_package_sha256=None, expected_model_files_sha256=None):
    loaded = packages.load_package(root, directory, eligible=True)
    if expected_package_sha256 is not None:
        packages.require(packages.files_hash(loaded[2]) == expected_package_sha256, 'package_changed')
    # Validate model identity before allocating a durable execution attempt.
    config = configuration(loaded[1], model_revision)
    config['model_files_sha256'] = model_fingerprint(model_path)
    if expected_model_files_sha256 is not None:
        packages.require(config['model_files_sha256'] == expected_model_files_sha256, 'model_changed')
    if dry_run:
        return await _publish(root, directory, model_revision, model_path=model_path,
                              dry_run=True, force=force, update_metadata=update_metadata, loaded=loaded, config=config)
    attempt_id = await attempts.start(loaded[0]['revision_id'])
    state = {'commit_started': False, 'receipt': False}
    try:
        result = await _publish(root, directory, model_revision, model_path=model_path,
                                force=force, update_metadata=update_metadata, loaded=loaded, config=config,
                                attempt_id=attempt_id, state=state)
        if not state['receipt']:
            async with session_scope() as db:
                await attempts.receipt(db, attempt_id, result['run_id'], 'unchanged')
                state['commit_started'] = True
        await attempts.reconcile(attempt_id)
        return dict(result, attempt_id=str(attempt_id))
    except BaseException as exc:
        if state['commit_started']:
            # A lost acknowledgement must never become a failed run or automatic retry.
            if isinstance(exc, asyncio.CancelledError):
                exc.attempt_id = str(attempt_id)
                raise
            raise attempts.AttemptUncertain(attempt_id) from None
        try:
            await attempts.finish(attempt_id, error_code='publication_cancelled' if isinstance(exc, asyncio.CancelledError)
                                  else 'publication_failed', cancelled=isinstance(exc, asyncio.CancelledError))
        except BaseException:
            if isinstance(exc, asyncio.CancelledError):
                exc.attempt_id = str(attempt_id)
                raise exc
            raise attempts.AttemptUncertain(attempt_id) from None
        exc.attempt_id = str(attempt_id)
        raise


async def _publish(root, directory, model_revision, *, model_path, dry_run=False, force=False,
                   update_metadata=False, loaded, config, attempt_id=None, state=None):
    record, payload, files = loaded
    config = dict(config)
    config.update(package_sha256=packages.files_hash(files),
                  metadata_sha256=packages.sha(files['metadata.json']),
                  fingerprint_version=1, embedding_preprocessing=PREPROCESSING)
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        revision, active, expected, resolved_record, missing = await snapshot(db, record, update_metadata)
        declared = sorted({r['revision_id'] for r in record['relations'] if r['type'] == 'supersedes'})
        if declared:
            config.update(declared_supersedes=declared, missing_supersedes=missing)
        source = await reuse_source(db, active, record)
        stored = await read_storage(db, source)
        expected_storage = storage_token(source, stored)
        unchanged = not force and storage_matches(active, stored, payload, config)
        same_metadata = metadata_matches(revision, resolved_record, expected)
        chunks, missing_chunks, reused = plan_vectors(payload, source, stored, config, force)
        reason = ('forced' if force else 'metadata_only' if unchanged and not same_metadata
                  else 'unchanged' if unchanged else 'first_publication' if source is None
                  else 'embedding_configuration_changed' if not compatible(source, config)
                  else 'storage_unverified' if source.configuration.get('content_hash') == config['content_hash']
                  else 'publication_changed')
        result = {'revision_id': record['revision_id'], 'missing_supersedes': missing,
                  'reason': reason, 'chunk_count': len(chunks),
                  'reused_chunk_count': 0 if unchanged else reused,
                  'encoded_chunk_count': 0 if unchanged else len(chunks) - reused,
                  'encoded_unique_text_count': 0 if unchanged else len({c['content'] for c in missing_chunks})}
        if unchanged and same_metadata:
            packages.require(packages.snapshot_files(directory) == files, 'package_changed')
            packages.require(model_fingerprint(model_path) == config['model_files_sha256'], 'model_changed')
            code = 'identical_active_run' if active.configuration == config else 'unchanged_publication'
            return dict(result, status='skipped', code=code, run_id=str(active.id))
    if dry_run:
        packages.require(packages.snapshot_files(directory) == files, 'package_changed')
        packages.require(model_fingerprint(model_path) == config['model_files_sha256'], 'model_changed')
        return dict(result, status='processed', code='dry_run')
    if missing_chunks and not unchanged:
        encoded = embed(model_path, copy.deepcopy(missing_chunks), model_revision)
        validate_vectors(encoded)
        packages.require([{k: v for k, v in c.items() if k != 'embedding'} for c in encoded] == missing_chunks,
                         'embedding_changed_content')
        vectors = {c['content']: c['embedding'] for c in encoded}
        for chunk in chunks:
            if 'embedding' not in chunk:
                chunk['embedding'] = list(vectors[chunk['content']])
    validate_vectors(chunks)
    packages.require([{k: v for k, v in c.items() if k != 'embedding'} for c in chunks] == payload['chunks'], 'embedding_changed_content')
    packages.require(model_fingerprint(model_path) == config['model_files_sha256'], 'model_changed')
    packages.require(packages.snapshot_files(directory) == files, 'package_changed')
    run_id = uuid4()
    async with session_scope() as db:
        await db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': IMPORT_LOCK})
        revision, active, actual, resolved_record, _ = await snapshot(db, record, update_metadata)
        packages.require(packages.snapshot_files(directory) == files, 'package_changed')
        packages.require(model_fingerprint(model_path) == config['model_files_sha256'], 'model_changed')
        packages.require(actual == expected, 'concurrent_publication_or_metadata_change')
        current_source = await reuse_source(db, active, record)
        current_storage = await read_storage(db, current_source)
        packages.require(storage_token(current_source, current_storage) == expected_storage,
                         'concurrent_publication_or_storage_change')
        db.info['publication_attempt_id'] = attempt_id
        await write_metadata(db, resolved_record, revision)
        if unchanged:
            await attempts.receipt(db, attempt_id, active.id, 'metadata_updated')
        if not unchanged and active:
            active.is_active = False
            await db.flush()
        if not unchanged:
            db.add(IngestionRun(id=run_id, revision_id=UUID(record['revision_id']), status='succeeded', is_active=True,
                source_sha256=record['sha256'], configuration=config,
                model_name=MODEL_NAME, model_revision=model_revision, embedding_dimensions=384, chunk_count=len(chunks)))
            await db.flush()
            db.add_all(DocumentChunk(run_id=run_id, chunk_index=c['chunk_index'], content=c['content'], source_location=c['source_location'],
                extraction_method=c['extraction_method'], embedding=c['embedding'],
                author_id=c.get('author_id'), role=c.get('role')) for c in chunks)
            await db.flush()
            if 'structured' in payload:
                await write_evidence(db, run_id, payload['structured'])
            await attempts.receipt(db, attempt_id, run_id, 'published')
        await db.flush()
        state['receipt'] = True
        state['commit_started'] = True
    return dict(result, status='processed', code='metadata_updated' if unchanged else 'published',
                run_id=str(active.id if unchanged else run_id))
