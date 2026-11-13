"""Exact cosine search of eligible public documents in a read-only snapshot."""
from datetime import date
import math
import hashlib
import json
from sqlalchemy import select, exists, or_, text
from src.database import session_scope
from src.database.models import DocumentChunk as Chunk, DocumentRevision as Revision
from src.database.models import IngestionRun as Run, DocumentAccessRule as Access
from src.ingestion.embeddings import MODEL_NAME


def eligibility(model_revision, fingerprint):
    today = date.today()
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
            'content': chunk.content, 'title': revision.title or '', 'source_path': revision.source_path,
            'effective_from': revision.effective_from.isoformat() if revision.effective_from else None,
            'effective_to': revision.effective_to.isoformat() if revision.effective_to else None,
            'applicability': revision.applicability, 'source_spans': chunk.source_location['spans'],
            'author_id': chunk.author_id, 'role': chunk.role}


async def search(vector, model_revision, fingerprint, top_k, *, detail_only=False, extra_vectors=()):
    today = date.today()
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
        # Active publications are immutable; IDs plus publication configuration bind
        # calibration to exactly the searchable corpus in this read snapshot.
        corpus = (await db.execute(select(Run.id, Run.revision_id, Run.configuration)
                  .join(Revision, Run.revision_id == Revision.id)
                  .where(*eligible, *compatible).order_by(Run.id))).all()
        result['corpus_fingerprint'] = hashlib.sha256(json.dumps(
            [(str(row[0]), str(row[1]), row[2]) for row in corpus],
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
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
