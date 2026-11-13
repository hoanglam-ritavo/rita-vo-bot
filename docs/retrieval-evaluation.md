# Local retrieval evaluation

This checklist evaluates source retrieval, separately from synthetic tests. The API uses native E5-small on CPU and does not generate answers, interpret applicability, count items or certify complete evidence.

## Prerequisites

- Record CPU, RAM, Python/runtime versions, E5 revision/file fingerprint and selected dataset revision/run IDs. Do not log credentials or commit private content.
- Use an existing immutable local E5 directory and already-published public documents. API startup does not download models, migrate or publish. If prerequisites are missing, report the affected checks as **not run**.
- Use `python -m src.cli.serve_api`, check `/health/live` and `/health/ready`, and open `/docs`. No separate inference service is needed. The application does not uninstall external software or stop services on the machine.

## Read-only checks

1. Before inspecting rankings, read the selected sources and author cases with expected source groups. Include Vietnamese paraphrases, conditions, exceptions, ambiguous questions, counts/exhaustive requests and unrelated questions. Store real-data cases and reports under ignored `.tmp/`.
2. Submit `POST /api/v1/retrieve` with `text` and optional `top_k`. Confirm full chunk content, order and provenance. The retired `/api/v1/ask` must return 404. Verify results contain neither generated prose nor confidence/evidence-completeness assessment. Additive relevance metadata is separate from completeness; see [online reranking](online-reranking.md).
3. On a stable database, compare the HTTP response with `python -m src.cli.retrieve_documents` using identical text, top-K and model bytes/revision. Match IDs, order, content, source spans and metadata must agree; distances may differ only within floating-point tolerance when device/runtime differs.
4. Exercise repeated HTTP requests. Confirm one E5 initialization per API lifecycle, record startup duration and retrieval/total times, and measure process RAM. Keep timings for failed attempts separately. These observations do not establish a latency SLA.
5. Verify 422 for invalid input, 429 for overlapping retrieval, 503 for dependency failures/model mismatch and 504 for timeout. Cancellation drains embedding work, so 504 can occur after the nominal 120-second deadline. Health endpoints must remain responsive while embedding runs.
6. Report empty valid results distinctly from errors. With candidates but no matching relevance profile, public retrieval returns no matches with `relevance_unavailable`. Below-threshold results, absent candidates and absent eligible documents have separate fallback reasons; none proves that no policy exists.

## Dataset acceptance

Use `python -m scripts.evaluate_retrieval` with pinned package/cases/model and an output path under `.tmp/`; see [README](../README.md#retrieval-evaluation) for the full command and case schema. Preserve the existing target: at least 90% complete evidence on detail/condition cases and all supported critical cases passing. Aggregate cases are reported separately; unsupported-only sets cannot satisfy the target. The API does not compute aggregate answers even when relevant chunks are returned.

Synthetic tests validate contracts, lifecycle and provenance handling, not real-model semantic quality. PostgreSQL integration tests require a separate disposable `TEST_DATABASE_URL` and are distinct from these read-only application-database checks. Missing prerequisites or skipped checks are not successful validation.

## Online reranking evaluation

Use the same model/pipeline settings for API, CLI and evaluator. `--rerank` enables pinned local ONNX scoring; `--rerank-candidates`, `--rerank-batch-size`, `--rerank-model-path` and `--rerank-profile` mirror API configuration. The evaluator records filtered match ranks, final logits, relevance and rerank time. See [the online gate](online-reranking.md#evaluation-and-rollout) for the minimum 100-question source-reviewed dataset, independent calibration/holdout/regression intent groups, labels for every retrieved candidate and at least 100 warmed CPU requests. Historical 40-case top-1 experiments do not meet that gate. Keep original cases and baseline reports; never tune on holdout outcomes.


## Filtered retrieval evaluation

Public retrieval now withholds unassessed candidates. This evaluator measures the filtered result and will report missing evidence without a matching schema-v2 profile. For offline candidate collection and per-passage labeling use the separate [online benchmark](online-reranking.md#evaluation-and-rollout); no public API/CLI parameter disables filtering. Historical unfiltered results do not establish current quality.

## Known evaluator reporting gap

`scripts.evaluate_retrieval` calls the public filtered service, which removes `candidate_order` before returning. Consequently, its legacy `before_rerank` field is empty and `candidate_grade` grades the filtered matches again. Neither field establishes candidate-pool recall or before/after reranking improvement. Use the separate offline benchmark for those measurements. The evaluator also omits `fallback_reason` from case reports: a report alone cannot distinguish every empty outcome. Inspect the public HTTP/CLI result for the explicit reason; do not infer one from a zero recall score.

Correcting those report fields and adding reason preservation is separate evaluator implementation work. Current tests exercise filtered evidence grading, input/storage drift and cleanup without certifying the legacy candidate metrics. No public filtering bypass is authorized by this limitation. See [current verification](../README.md#current-verification-status).
