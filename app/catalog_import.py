"""Validate and synchronize catalog metadata without extracting source content."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select, text

from app.asset_catalog import (NULLABLE_TEXT, STATES, compare, inventory, load_catalog,
                               reject_links, validate_schema)
from app.db import session_scope
from app.db.models import Document, DocumentAccessRule, DocumentRelation, DocumentRevision

IMPORT_LOCK = 728194031  # Shared by all full-catalog synchronization transactions.


class ImportRejected(ValueError):
    """A deliberately generic validation/conflict error safe for console output."""


@dataclass
class ImportResult:
    documents_added: int = 0
    revisions_added: int = 0
    revisions_updated: int = 0
    revisions_retired: int = 0
    warnings: int = 0


def prepare_catalog(root: Path):
    path = root / 'data/catalog/documents.json'
    reject_links(path)
    catalog = load_catalog(path)
    errors = validate_schema(catalog)
    if errors:
        raise ImportRejected(f'Catalog validation failed ({len(errors)} errors).')
    differences = compare(catalog, inventory(root / 'assets'))
    if any(differences[key] for key in ('added', 'missing', 'changed')):
        raise ImportRejected('Source inventory differs from catalog; reconcile it before import.')
    warnings = sum(len(differences[key]) for key in ('unresolved_duplicates', 'unrecognized_formats'))
    return catalog, warnings


def revision_values(record):
    values = {key: record[key] for key in NULLABLE_TEXT | set(STATES) |
              {'source_path', 'sha256', 'size_bytes', 'extension', 'review_notes'}}
    values.update(document_id=UUID(record['document_id']),
                  source_path_key=record['source_path'].casefold(), is_catalog_current=True)
    for key in ('effective_from', 'effective_to'):
        values[key] = date.fromisoformat(record[key]) if record[key] else None
    return values


async def synchronize(catalog, *, dry_run=False, warnings=0):
    """Apply one complete validated snapshot. Never commits inside session_scope."""
    errors = validate_schema(catalog)
    if errors:
        raise ImportRejected(f'Catalog validation failed ({len(errors)} errors).')
    result = ImportResult(warnings=warnings)
    async with session_scope() as db:
        if dry_run:
            # Consistent read-only snapshot; no advisory lock or writes.
            await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        else:
            await db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': IMPORT_LOCK})
        documents = {item.id: item for item in (await db.scalars(select(Document))).all()}
        revisions = {item.id: item for item in (await db.scalars(select(DocumentRevision))).all()}
        access = {}
        for item in (await db.scalars(select(DocumentAccessRule))).all():
            access.setdefault(item.revision_id, set()).add(item.group_name)
        relations = {}
        for item in (await db.scalars(select(DocumentRelation))).all():
            relations.setdefault(item.source_revision_id, set()).add((item.target_revision_id, item.relation_type))

        incoming = {UUID(r['revision_id']): r for r in catalog['documents']}
        changes = []
        for revision_id, record in incoming.items():
            values = revision_values(record)
            existing = revisions.get(revision_id)
            if existing and any(getattr(existing, key) != values[key]
                                for key in ('document_id', 'sha256', 'size_bytes')):
                raise ImportRejected('Revision identity or fingerprint changed; allocate a new revision ID.')
            groups = set(record['access_groups'])
            links = {(UUID(r['revision_id']), r['type']) for r in record['relations']}
            changed = (existing is None or any(getattr(existing, k) != v for k, v in values.items())
                       or groups != access.get(revision_id, set())
                       or links != relations.get(revision_id, set()))
            if changed:
                changes.append((revision_id, values, groups, links, existing))
                if existing is None:
                    result.revisions_added += 1
                else:
                    result.revisions_updated += 1
        new_documents = {UUID(r['document_id']) for r in incoming.values()} - documents.keys()
        result.documents_added = len(new_documents)
        retired = [r for r in revisions.values() if r.is_catalog_current and r.id not in incoming]
        result.revisions_retired = len(retired)
        if dry_run:
            return result

        now = datetime.now(timezone.utc)
        # Release partial unique keys before changing paths or selecting new revisions.
        for existing in retired + [change[4] for change in changes if change[4] is not None]:
            existing.is_catalog_current = False
        await db.flush()
        for document_id in new_documents:
            db.add(Document(id=document_id))
        await db.flush()
        affected_documents = {r.document_id for r in retired}
        for revision in retired:
            revision.updated_at = now
        for revision_id, values, groups, links, existing in changes:
            affected_documents.add(values['document_id'])
            if existing is None:
                db.add(DocumentRevision(id=revision_id, **values))
            else:
                for key, value in values.items():
                    setattr(existing, key, value)
                existing.updated_at = now
        await db.flush()
        # All target revisions now exist; replace only changed sets on incoming revisions.
        for revision_id, values, groups, links, existing in changes:
            if groups != access.get(revision_id, set()):
                await db.execute(delete(DocumentAccessRule).where(DocumentAccessRule.revision_id == revision_id))
                db.add_all(DocumentAccessRule(revision_id=revision_id, group_name=g) for g in sorted(groups))
            if links != relations.get(revision_id, set()):
                await db.execute(delete(DocumentRelation).where(DocumentRelation.source_revision_id == revision_id))
                db.add_all(DocumentRelation(source_revision_id=revision_id, target_revision_id=target,
                                            relation_type=kind) for target, kind in links)
        for document_id in affected_documents - new_documents:
            documents[document_id].updated_at = now
    return result
