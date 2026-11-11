"""Local E5 embeddings; model dependencies load only on invocation."""
MODEL_NAME = "intfloat/multilingual-e5-small"


def generate_embeddings(chunks: list[dict], *, model_revision=None, cache_folder=None) -> list[dict]:
    from sentence_transformers import SentenceTransformer

    options = {}
    if model_revision is not None:
        options["revision"] = model_revision
    if cache_folder is not None:
        options["cache_folder"] = str(cache_folder)
    model = SentenceTransformer(MODEL_NAME, **options)

    # E5 models use passage: for documents
    texts = [
        f"passage: {chunk['content']}"
        for chunk in chunks
    ]

    embeddings = model.encode(
        texts,
        batch_size=16,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    )

    if len(embeddings) != len(chunks):
        raise RuntimeError("Embedding count mismatch")
    for chunk, vector in zip(chunks, embeddings):
        chunk["embedding"] = vector.tolist()

    return chunks
