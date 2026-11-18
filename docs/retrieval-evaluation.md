# Local retrieval evaluation

This checklist evaluates source retrieval, separately from synthetic tests. The retrieval endpoint uses native E5-small on CPU and does not generate answers, interpret applicability, count items or certify complete evidence. The separate opt-in [template answer endpoint](answer-evaluation.md#semantic-template-answer-api) has its own contract and regression workflow.

## Prerequisites

- Record CPU, RAM, Python/runtime versions, E5 revision/file fingerprint and selected dataset revision/run IDs. Do not log credentials or commit private content.
- Use an existing immutable local E5 directory and already-published public documents. API startup does not download models, migrate or publish. If prerequisites are missing, report the affected checks as **not run**.
- Use `python -m src.cli.serve_api`, check `/health/live` and `/health/ready`, and open `/docs`. No separate inference service is needed. The application does not uninstall external software or stop services on the machine.

## Read-only checks

1. Before inspecting rankings, read the selected sources and author cases with expected source groups. Include Vietnamese paraphrases, conditions, exceptions, ambiguous questions, counts/exhaustive requests and unrelated questions. Store real-data cases and reports under ignored `.tmp/`.
2. Submit `POST /api/v1/retrieve` with `text` and optional `top_k`. Confirm full chunk content, order and provenance. The retired `/api/v1/ask` must return 404. Verify results contain neither generated prose nor confidence/evidence-completeness assessment. Additive relevance metadata is separate from completeness; see [online reranking](online-reranking.md).
3. On a stable database, compare HTTP with the retrieval CLI using identical text, top-K, local model bytes/revision, reranking and semantic settings. Both return unassessed candidates; no intent/profile/filter setting is used. IDs, order, content and metadata must agree, allowing floating-point distance tolerance across runtimes.
4. Exercise repeated HTTP requests. Confirm one E5 initialization per API lifecycle, record startup duration and retrieval/total times, and measure process RAM. Keep timings for failed attempts separately. These observations do not establish a latency SLA.
5. Verify 422 for invalid input, 429 for overlapping retrieval, 503 for dependency failures/model mismatch and 504 for timeout. Cancellation drains embedding work, so 504 can occur after the nominal 120-second deadline. Health endpoints must remain responsive while embedding runs.
6. Report empty valid results distinctly from errors. With filtering enabled, candidates but no matching relevance profile produce no matches with `relevance_unavailable`. Below-threshold results, absent candidates and absent eligible documents have separate fallback reasons; none proves that no policy exists. Explicitly unfiltered HTTP results preserve `unassessed` metadata and `relevance_filter_disabled` instead of certifying relevance.

## Dataset acceptance

Use `python -m scripts.evaluate_retrieval` with pinned package/cases/model and an output path under `.tmp/`; see [README](../README.md#retrieval-evaluation) for the full command and case schema. Preserve the existing target: at least 90% complete evidence on detail/condition cases and all supported critical cases passing. Aggregate cases are reported separately; unsupported-only sets cannot satisfy the target. The API does not compute aggregate answers even when relevant chunks are returned.

Synthetic tests validate contracts, lifecycle and provenance handling, not real-model semantic quality. PostgreSQL integration tests require a separate disposable `TEST_DATABASE_URL` and are distinct from these read-only application-database checks. Missing prerequisites or skipped checks are not successful validation.

Publication history retains original package/metadata byte hashes. Shared evaluation readers allow formatting changes and explicit metadata-only updates when current metadata/access/relations, canonical content/provenance/model identity and actual stored chunks/vectors/structured evidence match. They do not rewrite old runs or silently accept content drift. Input package/model bytes remain pinned for the duration of evaluation. See [publication compatibility](offline-indexing.md#publication-and-consistency).

## Online reranking evaluation

Use identical E5, reranker and semantic settings for HTTP, CLI and evaluation.
`--rerank` enables local ONNX ordering; model path, candidate count and batch size
arguments mirror active API settings. No profile/intent argument exists. Measure
candidate ranks and latency separately from task answer correctness. Keep source-
reviewed cases and independent question families; never tune on held-out outcomes.
