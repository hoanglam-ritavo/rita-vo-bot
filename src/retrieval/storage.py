"""Exact cosine search of eligible public documents in a read-only snapshot."""
from datetime import date
import math
from sqlalchemy import select, exists, or_, text
from src.database import session_scope
from src.database.models import DocumentChunk as Chunk, DocumentRevision as Revision
from src.database.models import IngestionRun as Run, DocumentAccessRule as Access
from src.ingestion.embeddings import MODEL_NAME


async def search(vector, model_revision, fingerprint, top_k):
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
        distance = Chunk.embedding.cosine_distance(vector).label('distance')
        query = (select(Chunk, Revision, distance).join(Run, Chunk.run_id == Run.id)
                 .join(Revision, Run.revision_id == Revision.id)
                 .where(*eligible, *compatible, Chunk.embedding.is_not(None))
                 .order_by(distance, Chunk.id).limit(top_k))
        for chunk, revision, score in (await db.execute(query)).all():
            if score is None or not math.isfinite(float(score)):
                raise ValueError('invalid_stored_vector_distance')
            result['matches'].append({
                'chunk_id': str(chunk.id), 'chunk_index': chunk.chunk_index, 'run_id': str(chunk.run_id),
                'document_id': str(revision.document_id), 'revision_id': str(revision.id),
                'content': chunk.content, 'cosine_distance': float(score), 'title': revision.title,
                'source_path': revision.source_path, 'version_label': revision.version_label,
                'effective_from': revision.effective_from.isoformat() if revision.effective_from else None,
                'effective_to': revision.effective_to.isoformat() if revision.effective_to else None,
                'applicability': revision.applicability, 'source_spans': chunk.source_location.get('spans', [])})
    result['status'] = 'matches_found' if result['matches'] else 'no_matches'
    return result
