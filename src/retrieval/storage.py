"""Exact cosine search of eligible public documents in a read-only snapshot."""
from src.business_calendar import business_today
import math
import hashlib
import json
from sqlalchemy import select, exists, or_, text
from src.database import session_scope
from src.database.models import DocumentChunk as Chunk, DocumentRevision as Revision
from src.database.models import IngestionRun as Run, DocumentAccessRule as Access
from src.ingestion.embeddings import MODEL_NAME


def corpus_identity(run_id, revision_id, configuration, revision, groups):
    """Publication identity, eligibility and provenance for evaluation drift detection."""
    metadata = {key: getattr(revision, key) for key in (
        'is_catalog_current', 'lifecycle_status', 'indexing_decision',
        'applicability', 'source_path', 'title', 'version_label')}
    metadata.update(access_groups=sorted(groups),
        effective_from=revision.effective_from.isoformat() if revision.effective_from else None,
        effective_to=revision.effective_to.isoformat() if revision.effective_to else None)
    return str(run_id), str(revision_id), configuration, metadata


def corpus_fingerprint(identities):
    return hashlib.sha256(json.dumps({'version': 2, 'publications': identities},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def eligibility(model_revision, fingerprint):
    today = business_today()
    public = exists(select(Access.revision_id).where(Access.revision_id == Revision.id, Access.group_name == 'public'))
    return (Run.status == 'succeeded', Run.is_active.is_(True), Revision.is_catalog_current.is_(True),
            Revision.indexing_decision == 'include', Revision.lifecycle_status == 'active', public,
            or_(Revision.effective_from.is_(None), Revision.effective_from <= today),
            or_(Revision.effective_to.is_(None), Revision.effective_to >= today),
            Run.model_name == MODEL_NAME, Run.model_revision == model_revision, Run.embedding_dimensions == 384,
            Run.configuration['model_files_sha256'].as_string() == fingerprint,
            Run.configuration['normalize_embeddings'].as_boolean().is_(True),
            Run.configuration['prefix'].as_string() == 'passage: ')


def match_record(chunk, revision):
    return {'chunk_id': str(chunk.id), 'chunk_index': chunk.chunk_index, 'run_id': str(chunk.run_id),
            'document_id': str(revision.document_id), 'revision_id': str(revision.id),
            'content': chunk.content, 'title': revision.title, 'source_path': revision.source_path,
            'version_label': revision.version_label,
            'effective_from': revision.effective_from.isoformat() if revision.effective_from else None,
            'effective_to': revision.effective_to.isoformat() if revision.effective_to else None,
            'applicability': revision.applicability, 'source_spans': chunk.source_location['spans'],
            'author_id': chunk.author_id, 'role': chunk.role}


async def read_references(references, model_revision, fingerprint):
    """Resolve authored references from one current, eligible publication snapshot.

    A reference is an address, never an authorization. Content/quote/context are
    checked again by the answer renderer. Missing addresses remain missing.
    """
    import uuid
    addresses = set()
    for ref in references:
        try:
            addresses.add((uuid.UUID(ref.document_id), uuid.UUID(ref.revision_id), ref.chunk_index))
        except ValueError:
            continue
    if not addresses:
        return []
    from sqlalchemy import tuple_
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        query = (select(Chunk, Revision).join(Run, Chunk.run_id == Run.id)
                 .join(Revision, Run.revision_id == Revision.id)
                 .where(*eligibility(model_revision, fingerprint),
                        tuple_(Revision.document_id, Revision.id, Chunk.chunk_index).in_(sorted(addresses))))
        rows = (await db.execute(query)).all()
        # Duplicate active publications must not select an arbitrary source.
        grouped = {}
        for chunk, revision in rows:
            key = (str(revision.document_id), str(revision.id), chunk.chunk_index)
            grouped.setdefault(key, []).append({**match_record(chunk, revision), 'source_sha256': revision.sha256})
        return [values[0] for _, values in sorted(grouped.items()) if len(values) == 1]


async def search(vector, model_revision, fingerprint, top_k, *, detail_only=False, extra_vectors=()):
    today = business_today()
    public = exists(select(Access.revision_id).where(Access.revision_id == Revision.id, Access.group_name == 'public'))
    eligible = (Run.status == 'succeeded', Run.is_active.is_(True),
                Revision.is_catalog_current.is_(True), Revision.indexing_decision == 'include',
                Revision.lifecycle_status == 'active', public,
                or_(Revision.effective_from.is_(None), Revision.effective_from <= today),
                or_(Revision.effective_to.is_(None), Revision.effective_to >= today))
    compatible = (Run.model_name == MODEL_NAME, Run.model_revision == model_revision,
                  Run.embedding_dimensions == 384,
                  Run.configuration['model_files_sha256'].as_string() == fingerprint,
                  Run.configuration['normalize_embeddings'].as_boolean().is_(True),
                  Run.configuration['prefix'].as_string() == 'passage: ')
    base = select(Run.id).join(Revision, Run.revision_id == Revision.id).where(*eligible)
    result = {'status': 'no_eligible_documents', 'matches': [], 'top_k': top_k,
              'notice': 'Nearest-neighbor candidates only; not an answer or confidence score.'}
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        if await db.scalar(base.limit(1)) is None:
            return result
        if await db.scalar(base.where(*compatible).limit(1)) is None:
            result['status'] = 'model_mismatch'
            return result
        # v2 binds metadata that changes eligibility and returned source provenance.
        corpus = (await db.execute(select(Run.id, Run.revision_id, Run.configuration, Revision)
                  .join(Revision, Run.revision_id == Revision.id)
                  .where(*eligible, *compatible).order_by(Run.id))).all()
        group_rows = (await db.execute(select(Access.revision_id, Access.group_name).where(
            Access.revision_id.in_([row[1] for row in corpus])))).all()
        groups_by_revision = {}
        for revision_id, group in group_rows:
            groups_by_revision.setdefault(revision_id, []).append(group)
        identities = []
        for run_id, revision_id, config, revision in corpus:
            identities.append(corpus_identity(run_id, revision_id, config, revision,
                                              groups_by_revision.get(revision_id, [])))
        result['corpus_fingerprint'] = corpus_fingerprint(identities)
        distance = Chunk.embedding.cosine_distance(vector).label('distance')
        query = (select(Chunk, Revision, distance).join(Run, Chunk.run_id == Run.id)
                 .join(Revision, Run.revision_id == Revision.id)
                 .where(*eligible, *compatible, Chunk.embedding.is_not(None))
                 .order_by(distance, Chunk.id).limit(top_k))
        if detail_only:
            query = query.where(or_(Chunk.role.is_(None), Chunk.role.not_in(['inventory', 'table'])))
        rows = (await db.execute(query)).all()
        result_lists = [rows]
        for extra in extra_vectors:
            # Select the original-query distance while ranking by the variant.
            alternate = query.order_by(None).order_by(Chunk.embedding.cosine_distance(extra), Chunk.id)
            result_lists.append((await db.execute(alternate)).all())
        lists = []
        for candidates in result_lists:
            lists.append(_records(candidates))
        if extra_vectors:
            from .reranking import fuse_candidates
            result['matches'] = fuse_candidates(lists, top_k)
        else:
            result['matches'] = lists[0]
    result['status'] = 'matches_found' if result['matches'] else 'no_matches'
    return result


def _records(rows):
    matches = []
    for chunk, revision, score in rows:
        if score is None or not math.isfinite(float(score)):
            raise ValueError('invalid_stored_vector_distance')
        matches.append({
            'chunk_id': str(chunk.id), 'chunk_index': chunk.chunk_index, 'run_id': str(chunk.run_id),
            'document_id': str(revision.document_id), 'revision_id': str(revision.id),
            'content': chunk.content, 'cosine_distance': float(score), 'title': revision.title,
            'author_id': chunk.author_id, 'role': chunk.role,
            'source_path': revision.source_path, 'version_label': revision.version_label,
            'effective_from': revision.effective_from.isoformat() if revision.effective_from else None,
            'effective_to': revision.effective_to.isoformat() if revision.effective_to else None,
            'applicability': revision.applicability, 'source_spans': chunk.source_location.get('spans', [])})
    return matches
