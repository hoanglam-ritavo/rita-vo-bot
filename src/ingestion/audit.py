"""Metadata audit snapshots recorded by the owning publication transaction."""
from sqlalchemy import select
from src.database.models import DocumentRevision, DocumentAccessRule, DocumentRelation, MetadataAudit


async def metadata_snapshot(db, revision):
    if revision is None:
        return None
    # Only metadata fields; no content, vectors, credentials or operational timestamps.
    fields = set(DocumentRevision.__table__.columns.keys()) - {'created_at', 'updated_at'}
    result = {key: getattr(revision, key) for key in fields}
    for key, value in list(result.items()):
        if value is not None and key in {'id', 'document_id', 'effective_from', 'effective_to'}:
            result[key] = str(value)
    result['access_groups'] = sorted(await db.scalars(select(DocumentAccessRule.group_name).where(
        DocumentAccessRule.revision_id == revision.id)))
    relations = (await db.execute(select(DocumentRelation.target_revision_id, DocumentRelation.relation_type)
        .where(DocumentRelation.source_revision_id == revision.id))).all()
    result['relations'] = sorted([{'revision_id': str(target), 'type': kind} for target, kind in relations],
                                 key=lambda row: (row['revision_id'], row['type']))
    return result


async def record_change(db, revision, before, attempt_id, event):
    after = await metadata_snapshot(db, revision)
    if before != after:
        db.add(MetadataAudit(revision_id=revision.id, attempt_id=attempt_id,
                             event=event, before=before, after=after))
