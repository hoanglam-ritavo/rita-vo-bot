"""Publish approved content without invoking parsers or rechunking."""
import copy
from importlib.metadata import version
import math
import re

from sqlalchemy import text

from app.database import session_scope
from app.ingestion import document_packages as packages
from app.ingestion import publish as lifecycle
from app.ingestion.embeddings import MODEL_NAME
from app.paths import workspace


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


def embed(root, chunks, model_revision):
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME, revision=model_revision,
                               cache_folder=str(workspace(root) / 'cache/huggingface'))
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


async def preflight_database(record):
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        await lifecycle.check_revision(db, record)


async def publish(root, directory, model_revision, *, dry_run=False, force=False):
    record, payload, approval, files = packages.load_approval(root, directory)
    config = configuration(payload, model_revision)
    await preflight_database(record)
    if dry_run:
        return {'status': 'processed', 'code': 'dry_run', 'revision_id': record['revision_id']}
    config['libraries'] = {name: version(name) for name in ('sentence-transformers', 'transformers', 'torch', 'numpy', 'tokenizers')}
    run_id, previous = await lifecycle.start_run(record, config, force)
    if run_id is None:
        return {**previous, 'revision_id': record['revision_id'], 'code': 'identical_active_run'}
    try:
        chunks = embed(root, copy.deepcopy(payload['chunks']), model_revision)
        validate_vectors(chunks)
        # Fail closed even if a faulty embedding adapter attempts to alter reviewed evidence.
        packages.require([{k: v for k, v in c.items() if k != 'embedding'} for c in chunks] == payload['chunks'], 'embedding_changed_content')
        artifacts = dict(files)
        artifacts['approval-manifest.json'] = artifacts.pop('manifest.json')
        artifacts['chunks.jsonl'] = b''.join(packages.encoded(c).replace(b'\n', b'') + b'\n' for c in chunks)
        artifacts['manifest.json'] = packages.encoded({'status': 'extraction_complete', 'run_id': str(run_id),
            'approval_id': approval['approval_id'], 'approval_manifest_sha256': packages.sha(files['manifest.json']),
            'configuration': config, 'chunk_count': len(chunks)})
        packages.write_package(workspace(root) / 'published_artifacts' / record['document_id'] / record['revision_id'], str(run_id), artifacts)
        current, _ = packages.selected(root, record['revision_id'], eligible=True)
        packages.require(current == record, 'catalog_changed')
        # This transaction rechecks DB metadata/active run under the import lock.
        # The immutable byte snapshot above is the evidence actually embedded and retained.
        await lifecycle.activate_run(record, run_id, previous, chunks)
        return {'status': 'processed', 'code': 'published', 'run_id': str(run_id), 'revision_id': record['revision_id']}
    except BaseException:
        try:
            await lifecycle.fail_run(run_id, 'approved_publication_failed')
        except Exception:
            print(f'Run status update failed: run={run_id}')
        raise
