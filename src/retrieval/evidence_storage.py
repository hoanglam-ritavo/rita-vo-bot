"""Bounded read-only scope discovery and same-run evidence expansion."""
from uuid import UUID
from sqlalchemy import select, text, func
from src.database import session_scope
from src.database.models import EvidenceScope, EvidenceItem, EvidenceValue
from .storage import Run, Revision, Chunk, eligibility, match_record


async def detail(matches, model_revision, fingerprint):
    """Resolve linked evidence for the anchor, rechecking every retained run."""
    anchor = matches[0]
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        items = list(await db.scalars(select(EvidenceItem).join(Run, EvidenceItem.run_id == Run.id)
            .join(Revision, Run.revision_id == Revision.id).where(*eligibility(model_revision, fingerprint),
                Run.id == UUID(anchor['run_id']),
                EvidenceItem.data['chunk_ids'].contains([anchor['author_id']])).limit(201)))
        linked = {c for item in items for c in item.data['chunk_ids']} | {anchor['author_id']}
        if len(items) > 200 or len(linked) > 20:
            return {'overflow': True}
        rows = (await db.execute(select(Chunk, Revision).join(Run, Chunk.run_id == Run.id)
            .join(Revision, Run.revision_id == Revision.id).where(*eligibility(model_revision, fingerprint),
                Run.id == UUID(anchor['run_id']), Chunk.author_id.in_(linked))
            .order_by(Chunk.chunk_index).limit(21))).all()
        if {c.author_id for c, _ in rows} != linked:
            raise ValueError('detail_evidence_unavailable')
        required = [match_record(c, revision) for c, revision in rows]
        ids = {m['chunk_id'] for m in required}
        # Retain other vector candidates only if they still pass all filters.
        optional_ids = [UUID(m['chunk_id']) for m in matches if m['chunk_id'] not in ids]
        optional = (await db.execute(select(Chunk, Revision).join(Run, Chunk.run_id == Run.id)
            .join(Revision, Run.revision_id == Revision.id).where(*eligibility(model_revision, fingerprint),
                Chunk.id.in_(optional_ids)).limit(21))).all()
        order = {m['chunk_id']: i for i, m in enumerate(matches)}
        extras = sorted([match_record(c, r) for c, r in optional], key=lambda m: order[m['chunk_id']])
        if len(required) + len(extras) > 20:
            return {'overflow': True}
        return {'overflow': False, 'matches': required + extras, 'required_ids': list(ids)}


async def candidates(vector, revision, fingerprint):
    distance = func.min(Chunk.embedding.cosine_distance(vector)).label('distance')
    query = (select(EvidenceScope, Revision.title, distance)
        .join(Run, EvidenceScope.run_id == Run.id).join(Revision, Run.revision_id == Revision.id)
        .join(Chunk, Chunk.run_id == Run.id)
        .where(*eligibility(revision, fingerprint), Chunk.role.in_(['inventory', 'table', 'procedure']),
               Chunk.embedding.is_not(None),
               EvidenceScope.data['chunk_ids'].contains(func.jsonb_build_array(Chunk.author_id)))
        .group_by(EvidenceScope.run_id, EvidenceScope.author_id, Revision.id)
        .order_by(distance, EvidenceScope.run_id, EvidenceScope.author_id).limit(6))
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        rows = (await db.execute(query)).all()
        return {'overflow': len(rows) > 5, 'scopes': [
            {'key': f'C{i+1}', 'run_id': str(s.run_id), 'scope_id': s.author_id, 'name': s.data['name'],
             'description': s.data['description'], 'coverage': s.data['coverage'],
             'operations': s.data['operations'], 'title': title}
            for i, (s, title, _) in enumerate(rows[:5])]}


async def expand(candidate, model_revision, fingerprint):
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        row = (await db.execute(select(EvidenceScope, Revision).join(Run, EvidenceScope.run_id == Run.id)
            .join(Revision, Run.revision_id == Revision.id).where(*eligibility(model_revision, fingerprint),
                Run.id == UUID(candidate['run_id']), EvidenceScope.author_id == candidate['scope_id']))).first()
        if row is None:
            raise ValueError('scope_unavailable_or_changed')
        scope, revision = row
        data = scope.data
        ids = data['item_ids']
        if len(ids) > 200:
            return {'overflow': True}
        items = list(await db.scalars(select(EvidenceItem).where(
            EvidenceItem.run_id == scope.run_id, EvidenceItem.author_id.in_(ids)).limit(201)))
        if {x.author_id for x in items} != set(ids):
            raise ValueError('scope_members_missing')
        values = list(await db.scalars(select(EvidenceValue).where(
            EvidenceValue.run_id == scope.run_id, EvidenceValue.item_id.in_(ids)).limit(201)))
        chunk_ids = set(data['chunk_ids']) | {c for item in items for c in item.data['chunk_ids']}
        if len(values) > 200 or len(chunk_ids) > 20:
            return {'overflow': True}
        chunks = list(await db.scalars(select(Chunk).where(Chunk.run_id == scope.run_id,
            Chunk.author_id.in_(chunk_ids)).order_by(Chunk.chunk_index).limit(21)))
        if {c.author_id for c in chunks} != chunk_ids:
            raise ValueError('scope_chunks_missing')
        ordered = {x.author_id: x.data for x in items}
        return {'overflow': False, 'scope': data, 'items': [ordered[i] for i in ids],
                'values': [v.data for v in values], 'matches': [match_record(c, revision) for c in chunks]}
