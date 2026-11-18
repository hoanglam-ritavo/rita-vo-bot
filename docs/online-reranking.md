# Online CPU reranking

## Local model and configuration

Reranking sorts document candidates; it does not authorize template answers or
certify relevance. There is no threshold/profile loading or learned scoring layer.
See [catalog answering](catalog-answering.md) for the replacement answer gate.

The pinned model is `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, revision
`1427fd652930e4ba29e8149678df786c240d8825`. Provision `model_quint8_avx2.onnx`
and its tokenizer/config files directly in `models/`. The ONNX SHA-256 is
`6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b`.
Startup validates all files against the allowlist in `src/retrieval/reranking.py`.
Install `requirements-example.txt`; no model downloads occur at runtime.

| Setting | Default | Contract |
| --- | --- | --- |
| RERANK_ENABLED | false | Enable pinned local CPU inference |
| RERANK_MODEL_PATH | models | Repository-relative or absolute local directory |
| RERANK_MODEL_FILE | model_quint8_avx2.onnx | Only the pinned filename |
| RERANK_CANDIDATE_COUNT | 30 | Fixed candidate pool |
| RERANK_BATCH_SIZE | 4 | 1–16 windows per batch |

CPUExecutionProvider requires AVX2, four intra-op threads and one inter-op thread.
Models initialize once and must remain immutable until shutdown. Missing/corrupt
models fail startup; inference failures return an operational error, without
silently falling back to vector search. Reranking is independent of direct
catalog-evidence reads, but still used for explicit document lookup.

## Retrieval and response semantics

NFC/whitespace normalization and two bounded typo corrections are retained.
Guarded semantic alternatives retrieve candidates in one readonly snapshot,
deduplicate by chunk ID and use reciprocal rank fusion (constant 60). Each
query searches up to 30 candidates; the selected pool is reranked. Long passages
use 512-token windows with 64-token overlap and the maximum raw logit.
No content or conditions are silently truncated to fit the reranker.
Questions leaving at most 64 passage tokens are rejected with 422.

Return at most top-K (1–20). Before top-K, the optional [leave policy](leave-retrieval.md) can stably reorder source-matched buckets without changing scores. Baseline ordering uses reranker score, original-query
cosine distance and chunk ID. Without reranking, use vector order within the existing top-K pool. Callers preserve server order; raw logits are not the final sort key when metadata applies. Nonempty
results are unassessed, with `relevance_not_assessed` and
`evidence_completeness_not_assessed`. Logits are not confidence probabilities.
Empty outcomes retain `no_candidates` / `no_eligible_documents`; there is no
profile-related empty fallback. HTTP and CLI share this behavior.

## Evaluation and rollout

Use the retained general [retrieval evaluator](retrieval-evaluation.md) and
catalog regression checks. Calibrators, learned-scoring fitters, intent training,
profile promotion and their Colab kits are retired. No accepted profile is required.
Preserve existing model hash, CPU, timeout and cancellation checks. Readiness
checks dependencies, not business correctness or coverage.

## Prepare baseline calibration inputs

Retired. Use [source impact and refresh](catalog-answering.md#updating-chunks).
