"""Relational projections of immutable authored v3 evidence snapshots."""
import copy
from decimal import Decimal
from sqlalchemy import select
from src.database.models import EvidenceScope, EvidenceItem, EvidenceValue, ScopeItem, ScopeChunk, ItemChunk


async def write_evidence(db, run_id, data):
    db.add_all(EvidenceScope(run_id=run_id, author_id=r['id'], data=r) for r in data['scopes'])
    db.add_all(EvidenceItem(run_id=run_id, author_id=r['id'], parent_id=r['parent_id'], data=r) for r in data['items'])
    await db.flush()
    db.add_all(EvidenceValue(run_id=run_id, author_id=r['id'], item_id=r['item_id'],
                            numeric_value=Decimal(r['value']) if r['type'] == 'decimal' else None,
                            data=r) for r in data['values'])
    for scope in data['scopes']:
        db.add_all(ScopeItem(run_id=run_id, scope_id=scope['id'], item_id=key, position=i)
                   for i, key in enumerate(scope['item_ids']))
        db.add_all(ScopeChunk(run_id=run_id, scope_id=scope['id'], chunk_id=key, position=i)
                   for i, key in enumerate(scope['chunk_ids']))
    for item in data['items']:
        db.add_all(ItemChunk(run_id=run_id, item_id=item['id'], chunk_id=key, position=i)
                   for i, key in enumerate(item['chunk_ids']))


async def read_evidence(db, run_id):
    """Reject drift before any reader uses authored snapshots or projected links."""
    from .document_packages import require
    result = {}
    records = {}
    for key, model in [('scopes', EvidenceScope), ('items', EvidenceItem), ('values', EvidenceValue)]:
        rows = list(await db.scalars(select(model).where(model.run_id == run_id)))
        records[key] = rows
        result[key] = sorted((copy.deepcopy(r.data) for r in rows), key=lambda r: r['id'])
        require(all(r.author_id == r.data['id'] for r in rows), 'evidence_projection_mismatch')
    for key, model, owner, member, collection in [
        ('scopes', ScopeItem, 'scope_id', 'item_id', 'item_ids'),
        ('scopes', ScopeChunk, 'scope_id', 'chunk_id', 'chunk_ids'),
        ('items', ItemChunk, 'item_id', 'chunk_id', 'chunk_ids'),
    ]:
        rows = list(await db.scalars(select(model).where(model.run_id == run_id)))
        actual = sorted((getattr(r, owner), r.position, getattr(r, member)) for r in rows)
        expected = sorted((r['id'], i, value) for r in result[key] for i, value in enumerate(r[collection]))
        require(actual == expected, 'evidence_projection_mismatch')
    for row in records['items']:
        require(row.parent_id == row.data['parent_id'], 'evidence_projection_mismatch')
    for row in records['values']:
        expected = Decimal(row.data['value']) if row.data['type'] == 'decimal' else None
        require(row.item_id == row.data['item_id'] and row.numeric_value == expected, 'evidence_projection_mismatch')
    return result
