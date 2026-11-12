"""Shared local embedding runtime; importing this module never loads a model."""
import math
from pathlib import Path

from app.catalog.inventory import reject_links, file_fingerprint
from app.ingestion import document_packages as packages

MODEL_NAME = 'intfloat/multilingual-e5-small'


def load_model(model_path):
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(str(model_path), local_files_only=True, trust_remote_code=False)
    packages.require(model.get_sentence_embedding_dimension() == 384, 'model_dimensions')
    return model


def prepare_inputs(model, chunks):
    texts = ['passage: ' + chunk['content'] for chunk in chunks]
    limit = min(model.max_seq_length, model.tokenizer.model_max_length)
    packages.require(type(limit) is int and limit > 0, 'invalid_model_token_limit')
    checks = []
    for chunk, content in zip(chunks, texts):
        count = len(model.tokenizer(content, add_special_tokens=True, truncation=False)['input_ids'])
        checks.append({'chunk_index': chunk.get('chunk_index', len(checks)),
                       'token_count': count, 'status': 'passed' if count <= limit else 'exceeded'})
    return texts, limit, checks


def encode_chunks(model, texts, chunks):
    vectors = model.encode(texts, batch_size=16, normalize_embeddings=True,
                           convert_to_numpy=True, show_progress_bar=False)
    packages.require(len(vectors) == len(chunks), 'embedding_count')
    for chunk, vector in zip(chunks, vectors):
        chunk['embedding'] = vector.tolist()
    validate_vectors(chunks)
    return chunks


def embed(model_path, chunks, model_revision):
    model = load_model(model_path)
    texts, _, checks = prepare_inputs(model, chunks)
    packages.require(all(c['status'] == 'passed' for c in checks), 'chunk_exceeds_model_limit')
    return encode_chunks(model, texts, chunks)


def validate_vectors(chunks):
    packages.require(bool(chunks), 'empty_chunks')
    for index, chunk in enumerate(chunks):
        vector = chunk.get('embedding')
        packages.require(chunk['chunk_index'] == index and isinstance(vector, list) and len(vector) == 384
                         and all(type(v) in (int, float) and math.isfinite(v) for v in vector), 'invalid_vectors')


def model_fingerprint(path):
    path = Path(path)
    reject_links(path)
    packages.require(path.is_dir(), 'local_model_missing')
    files = {}
    queue = [path]
    while queue:
        for item in sorted(queue.pop().iterdir()):
            reject_links(item)
            if item.is_dir():
                queue.append(item)
            elif item.is_file():
                files[item.relative_to(path).as_posix()] = file_fingerprint(item)['sha256']
            else:
                raise packages.Rejected('nonregular_model_file')
    packages.require(bool(files), 'local_model_empty')
    return packages.canonical_hash(files)
