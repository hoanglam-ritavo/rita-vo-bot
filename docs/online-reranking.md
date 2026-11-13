# Online CPU reranking

This opt-in pipeline retrieves and filters candidates without modifying packages, publications, database schemas or stored embeddings. It does not generate answers, establish personal eligibility, or certify complete evidence. Missing policy content still requires a separately authorized offline update.

## Local model and configuration

The supported model is `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, pinned to revision `1427fd652930e4ba29e8149678df786c240d8825`. Place `model_quint8_avx2.onnx` directly in `models/`, alongside `config.json`, `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json` and `sentencepiece.bpe.model` from that revision. These local artifacts are ignored by Git. Startup validates every file against the pinned SHA-256 allowlist in `src/retrieval/reranking.py`; renaming an unrelated model does not make it compatible.

The ONNX SHA-256 is `6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b`. The official model is available at [Hugging Face](https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1/tree/1427fd652930e4ba29e8149678df786c240d8825). Download matching files explicitly during provisioning, never at application startup. Preserve the original downloaded file when preparing another environment.

Install `requirements-example.txt`. ONNX Runtime uses `CPUExecutionProvider`, four intra-op threads and one inter-op thread. The machine must support AVX2. ONNX and tokenizer initialize once; files must remain immutable until shutdown. Missing/corrupt files fail startup. A configured reranker failure returns a sanitized 503, with no automatic vector-only fallback.

| Setting | Default | Meaning |
| --- | --- | --- |
| `RERANK_ENABLED` | `false` | Explicit opt-in; keep disabled until rollout gates pass |
| `RERANK_MODEL_PATH` | `models` | Relative to the bot repository root |
| `RERANK_MODEL_FILE` | `model_quint8_avx2.onnx` | Only the pinned artifact is supported |
| `RERANK_CANDIDATE_COUNT` | `30` | Exactly 30; independent of requested top-K |
| `RERANK_BATCH_SIZE` | `4` | 1–16 windows per inference batch |
| `RERANK_PROFILE_PATH` | unset | Local schema-v2 per-passage calibration profile; missing/stale profile returns an empty fallback |

The API reads these settings with the existing explicit `.env` loader. CLI and evaluation use explicit arguments instead: `--rerank`, `--rerank-model-path`, `--rerank-candidates`, `--rerank-batch-size`, `--rerank-profile`. Use identical settings for API/CLI comparisons. Restart with `RERANK_ENABLED=false` for an explicit rollback.

## Retrieval and response semantics

Enabled retrieval normalizes NFC and whitespace. An exact phrase-boundary allowlist creates at most one additional query: `thai sảng` becomes `thai sản`; `nguyên đáng` becomes `nguyên đán`. Original and corrected queries both retrieve candidates. No general spelling model, hosted service, or semantic rewriting is used.

Each query retrieves up to 30 candidates in one shared read-only REPEATABLE READ snapshot. Eligibility/model compatibility filters remain unchanged. Results are deduplicated by chunk ID and fused using reciprocal rank fusion (constant 60), with ID tie-breaking. Inventory/table roles remain searchable. The selected pool is reranked by the corrected query when present.

The reranker consumes XLM-R query/passage pairs, without E5 prefixes. Each pair fits 512 tokens. Long passages use overlapping windows (64-token overlap) and the maximum window score; all passage tokens are processed. Questions leaving 64 or fewer passage tokens return 422 rather than being truncated. This is an additional enabled-mode limit. Complete original chunk content and provenance are returned, including conditions outside the highest-scoring window. A high window score therefore does not certify every condition or full evidence coverage.

`top_k` remains 1–20. Matches sort by descending `rerank_score`, then ascending original-query `cosine_distance`, then chunk ID. Cosine distances are always relative to the normalized original query, including candidates discovered only through its corrected variant; they no longer determine the final order.

The additive HTTP fields are:

- `matches[].rerank_score`: raw finite logit, or null in vector-only mode. Neither logits nor sigmoid values are calibrated confidence probabilities.
- `relevance.status`: `unassessed`, `low_relevance`, `relevant_candidates`, or `no_candidates`.
- `relevance.warnings`: always includes `evidence_completeness_not_assessed`; low relevance also includes `low_relevance`.
- `timings.rerank_seconds`: tokenizer and ONNX scoring time; included in existing retrieval/total timing, not an additional interval to sum.

Existing top-level statuses remain unchanged. `top_k` is an upper bound, never a target to fill. After reranking, retain only passages with raw score **greater than or equal to** the calibrated threshold, then take at most top-K. The remaining passages retain their original content and provenance. A matching profile is required even for a very high raw score; there is no universal cutoff or vector-only fallback.

Every HTTP/CLI result includes required nullable `fallback_reason`:

| Status | Matches | Reason |
| --- | --- | --- |
| `matches_found` | 1 through top-K | `null` |
| `no_matches` | empty | `below_relevance_threshold`, `relevance_unavailable`, or `no_candidates` |
| `no_eligible_documents` | empty | `no_eligible_documents` |

Eligibility/empty candidate outcomes take precedence over unavailable assessment. Disabled reranking, missing profiles, legacy profiles, failed holdout or mismatched fingerprints produce `relevance_unavailable`, not proof that no policy exists. Invalid configured profile JSON/fields fail model startup. Model/database/timeout failures remain operational errors. Relevance metadata describes the candidate assessment; it never certifies evidence completeness. Low-scoring and unassessed candidates are not exposed through HTTP or the retrieval CLI.

Schema-v2 profiles contain `schema_version: 2`, `fingerprint`, `corpus_fingerprint`, finite raw-logit `threshold`, and `holdout_passed: true`. Model artifacts, revision, pipeline version, candidate count, batch size and ONNX Runtime version participate in the model fingerprint. The corpus fingerprint covers compatible eligible active run IDs, revision IDs and publication configurations, computed in the same read-only snapshot as retrieval. Publications must remain immutable; changed publications/eligibility require new calibration. Legacy top-1 profiles cannot authorize per-passage filtering.

One request runs at a time in the API; concurrent calls still receive 429. Cancellation and shutdown drain active model work; the existing 120-second deadline is retained. The five-second p95 is a rollout target, not the HTTP deadline. Operational logs contain counts, status and timings, not question/document content.

## Evaluation and rollout

`scripts.evaluate_retrieval` evaluates the public filtered result. Without a valid profile it observes empty fallbacks. `scripts.benchmark_online_retrieval` has a separate offline-only candidate path; no HTTP parameter or public retrieval CLI option can bypass filtering.

Prepare a `.tmp/` manifest with `datasets` containing `package` and `cases` paths. Retain the existing pinned revision/chunks hash and `required_groups` schema. Each case requires a globally unique ID/question, `split` (`calibration`, `holdout`, `regression`), `relevant` boolean, and `intent_group`. Related paraphrases must share one intent group and never cross splits. Known inspected questions belong in regression. All evidence and relevance labels require source review, not model-score inference.

The gate requires at least 100 questions: at least 20 supported and 20 unsupported each in calibration and holdout, plus 20 regression questions. Use `--collect-only` first to obtain candidate identities for labeling; this permits smaller exploratory sets, emits no profile and never reports rollout readiness. Every case in a final evaluation needs `candidate_labels`, a dictionary mapping **every** retrieved top-30 chunk UUID to a boolean (directly supports the requested answer versus merely related/irrelevant). The exact UUID set must match the current retrieval. Source review may accept multiple chunks/documents; required evidence groups alone are not exhaustive relevance labels. Do not tune against holdout rankings. Reports retain IDs, ranks, scores and decisions, not question/excerpt text.

```powershell
.\env\Scripts\python.exe -m scripts.benchmark_online_retrieval --manifest .tmp/fallback-evaluation/manifest.json --model-path .tmp/models/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3 --model-revision 614241f622f53c4eeff9890bdc4f31cfecc418b3 --rerank --collect-only --output .tmp/fallback-evaluation/candidates.json
```

After source labeling and freezing the dataset, omit `--collect-only` and choose a new output filename. Evaluation is read-only: no migration, publication or stored-vector changes. Package/storage/model/corpus identities must remain stable throughout. Real manifests/reports remain under ignored `.tmp/`; committed tests use fictional content.

Calibration picks the lowest finite threshold strictly above all calibration negative-passage scores. Gates require no irrelevant returned passages (checked through top-K 20), no unsupported acceptances, and at least 90% supported complete evidence at top-K 5 on both calibration and holdout. All critical and required-top-1 cases must pass after filtering. Reports include false-empty counts, removed/retained counts, candidate-pool completeness and post-filter group ranks. At least 100 sequential warmed requests must have p95 at most five seconds. Latency uses the public filtering path with a provisional in-memory threshold; the profile is not written until all gates pass.

A schema-v2 profile is emitted only if every gate passes. Otherwise leave assessment unavailable; do not lower gates, widen candidate counts, substitute a model or introduce document-specific rules. The historical 40-question top-1 evaluation is not sufficient for this gate. Passing synthetic tests does not establish real retrieval quality.

Deploy bot and backend together during maintenance, draining old processes first. The backend requires the new reason field; an old bot response is invalid, not empty evidence. Until a matching profile is provisioned, available documents yield `no_matches/relevance_unavailable`. `/health/ready` still measures dependencies, not calibration quality. Existing portal status handling remains compatible but does not yet display reason-specific messages. Logs include only counts, status/reason and timings.
