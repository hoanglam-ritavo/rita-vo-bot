"""Exact-text reuse and storage verification without a database or model."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.database.runtime import run_async
from src.ingestion import publication_state as state


def published():
    config = dict(pipeline='agent-json-v2', source_sha256='a' * 64, content_hash='evidence',
                  model_name='e5', model_revision='a' * 40, model_files_sha256='files',
                  embedding_dimensions=384, normalize_embeddings=True, prefix='passage: ', batch_size=16)
    run = SimpleNamespace(id='run', status='succeeded', configuration=dict(config), chunk_count=3,
                          source_sha256='a' * 64, model_name='e5', model_revision='a' * 40,
                          embedding_dimensions=384)
    chunks = [dict(chunk_index=i, content=text, source_location={'spans': []},
                   extraction_method='native', embedding=[float(i + 1)] + [0.] * 383)
              for i, text in enumerate(['A', 'B', 'C'])]
    return run, config, {'chunks': chunks, 'structured': {}}


@pytest.mark.parametrize('texts,encoded,reused', [
    (['C', 'A'], [], 2),
    (['A', 'new', 'B', 'new', 'C'], ['new'], 3),
    (['new', 'B', 'other'], ['new', 'other'], 1),
    (['A ', 'B', 'C'], ['A '], 2),
])
def test_reuse_uses_exact_text_not_chunk_position(texts, encoded, reused):
    run, config, storage = published()
    payload = {'chunks': [dict(chunk_index=i, content=t) for i, t in enumerate(texts)]}
    before = copy.deepcopy(storage)
    chunks, missing, count = state.plan_vectors(payload, run, storage, config)
    assert count == reused
    assert [c['content'] for c in missing] == encoded
    assert [c['chunk_index'] for c in missing] == list(range(len(missing)))
    assert [c['chunk_index'] for c in chunks] == list(range(len(texts)))
    assert storage == before
    for chunk in chunks:
        if chunk['content'] in ['A', 'B', 'C']:
            assert chunk['embedding'][0] == ['A', 'B', 'C'].index(chunk['content']) + 1


@pytest.mark.parametrize('field,value', [
    ('model_files_sha256', 'changed'), ('model_revision', 'b' * 40),
    ('normalize_embeddings', False), ('prefix', 'query: '),
    ('embedding_dimensions', 768), ('embedding_preprocessing', 'future'),
    ('fingerprint_version', 2), ('pipeline', 'unknown'),
])
def test_incompatible_legacy_or_future_configuration_is_not_reused(field, value):
    run, config, storage = published()
    run.configuration[field] = value
    payload = {'chunks': [{k: v for k, v in c.items() if k != 'embedding'} for c in storage['chunks']]}
    _, missing, reused = state.plan_vectors(payload, run, storage, config)
    assert reused == 0 and len(missing) == 3


@pytest.mark.parametrize('vector', [None, [0.], [float('nan')] * 384, [float('inf')] * 384, [True] * 384])
def test_invalid_reuse_vectors_are_rejected(vector):
    assert state.vector_values(vector) is None


def test_force_encodes_all_chunks_and_does_not_mutate_source():
    run, config, storage = published()
    payload = {'chunks': [dict(chunk_index=i, content='A') for i in range(2)]}
    chunks, missing, reused = state.plan_vectors(payload, run, storage, config, force=True)
    assert reused == 0 and missing == payload['chunks'] and chunks == payload['chunks']


def test_read_storage_detaches_pgvector_arrays_and_evidence():
    import numpy as np
    run, _, _ = published()
    row = SimpleNamespace(chunk_index=0, content='A', source_location={'spans': []},
                          extraction_method='native', author_id='a', role='detail',
                          embedding=np.array([1.] + [0.] * 383))
    db = SimpleNamespace(scalars=AsyncMock(side_effect=[
        [row], [], [], [], [], [], []]))
    result = run_async(state.read_storage(db, run))
    assert result['chunks'][0]['embedding'] == [1.] + [0.] * 383
    assert result['chunks'][0]['author_id'] == 'a'
    assert result['structured'] == {'scopes': [], 'items': [], 'values': []}
    result['chunks'][0]['source_location']['spans'].append('new')
    assert row.source_location == {'spans': []}
