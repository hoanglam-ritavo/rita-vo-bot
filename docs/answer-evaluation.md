# Offline answer evaluation before endpoint integration

The answer endpoint is **not implemented**. This is stage one of the approved
measure-before-integrating plan. Public HTTP/CLI retrieval continues to return
filtered excerpts only. No backend or portal changes are part of this stage.

`scripts.evaluate_answers` compares the same accepted evidence in three forms:
retrieval excerpts, an unverified generated draft, and the draft after code and
model verification. It never bypasses public relevance filtering. The historical
40-question answer/retrieval experiments do not satisfy this experiment's gates.

## Prerequisites and stop conditions

1. Meet the existing [retrieval calibration gates](online-reranking.md#evaluation-and-rollout).
   A schema-v2 profile must match the current model/pipeline and immutable eligible
   publication corpus. Missing or stale assessment stops the experiment before
   generation. Do not synthesize passage labels from scores or lower the gates.
2. Prepare 60 source-reviewed Vietnamese cases before examining model output.
   The distribution is 20 supported, 10 partial, 10 absent, 10 ambiguous/personal,
   and 10 adversarial/exception/conflict cases. Each category is divided equally
   between development and holdout. Paraphrases share an intent group and never
   cross splits. Freeze the cases and prompt before the full run; do not tune on
   inspected holdout results. An inspected holdout needs replacement before a new
   acceptance claim.
3. Explicitly provision a local `llama-server` executable and Qwen3-4B Q4_K_M GGUF.
   Record the upstream model revision and runtime release/commit in the local
   experiment notes, and pass SHA-256 hashes of both artifacts to the tool.
   No tool command installs dependencies, downloads weights, changes `.env`,
   publishes documents, or migrates storage.

The reference machine is an Intel i5-10505 (6 physical/12 logical cores) with
approximately 16 GiB RAM. The experiment uses six CPU threads, zero GPU layers,
one inference slot, an 8,192-token context, thinking disabled, and output budgets
of 1,024 generation tokens and 512 verifier tokens. It performs one generation
and at most one verification call, with no repair loop.

Use the official [Qwen GGUF model](https://huggingface.co/Qwen/Qwen3-4B-GGUF) and
[llama.cpp server contract](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).
The selected executable must support `/apply-template`, `/tokenize`, structured
chat completions, `--reasoning off`, `--no-context-shift` and `--no-webui`.
Actual compatibility must be checked with that pinned executable; mocked tests
do not establish compatibility with an installed runtime. The managed process
is hidden on Windows, loopback-only and terminated on completion/cancellation.
It never attaches to an existing server or calls a hosted model.

## Case manifest

All real cases and artifacts must live under ignored `.tmp/`. The manifest uses:

```json
{
  "schema_version": 1,
  "source_reviewed": true,
  "packages": {
    "chunks/<document-id>": {
      "metadata.json": "<sha256>",
      "chunks.json": "<sha256>",
      "extraction.json": "<sha256>",
      "source.pdf": "<sha256>"
    }
  },
  "cases": [
    {
      "id": "development-supported-01",
      "intent_group": "fictional-contract-eligibility",
      "split": "development",
      "category": "supported",
      "critical": true,
      "question": "Which contracts qualify under the fictional policy?",
      "expected_status": "answered",
      "expected": "Permanent contracts qualify; temporary contracts are excluded.",
      "source_refs": [
        {"package": "chunks/<document-id>", "chunk_id": "<author-chunk-id>"}
      ]
    }
  ]
}
```

This fragment illustrates the schema, not a runnable or reviewed dataset.
`packages` must pin every file in each package, including its source snapshot.
`source_refs.chunk_id` is the authored package ID, not a database UUID. References
are review expectations and are **not** supplied to the generator as oracle
evidence. The generator receives only the public retrieval result. Before and
after inference the runner checks pinned packages against publication storage,
including content, spans, vectors and active run identity, using the existing
read-only evaluator. Package changes invalidate the manifest.

Valid categories are `supported`, `partial`, `absent`, `ambiguous`, `adversarial`.
Valid expected statuses are `answered`, `partial`, `needs_clarification`,
`insufficient_evidence`, `unsupported`. A boolean source-review marker records
author assertion; it cannot prove semantic source accuracy. Original sources
must actually be reviewed. Never mark placeholder cases as reviewed.

## Commands

Run from the repository root with `requirements-dev.txt` and
`requirements-example.txt` already installed. Preflight requires no database
connection or model inference:

```powershell
.\env\Scripts\python.exe -m scripts.evaluate_answers preflight --cases .tmp/answer-study/cases.json --output .tmp/answer-study/preflight-01
```

Without artifact arguments, preflight explicitly reports missing model/runtime.
For a full run, replace every placeholder with an explicitly provisioned file
or its verified hash:

```powershell
.\env\Scripts\python.exe -m scripts.evaluate_answers run --cases .tmp/answer-study/cases.json --runtime .tmp/runtime/llama-server.exe --runtime-sha256 <runtime-sha256> --model .tmp/models/qwen3-4b-q4_k_m.gguf --model-sha256 <model-sha256> --output .tmp/answer-study/run-01
```

The output directory must not exist. The process uses port 8099 by default
(`--port` can change it); an occupied port fails rather than attaching to it.
Retrieval settings come from the existing explicit configuration loader.

The full run includes a fictional warm-up, then all 60 frozen cases. A 120-second
deadline covers each question's retrieval, generation and verification. On an
operational failure/timeout, the experiment stops and terminates its owned model
process; it does not continue while unknown inference may still be running.
Existing retrieval cancellation drains worker threads, so safe cleanup can
exceed the deadline. Timings include that wait and are not recorded as success.

Context counting includes formatted messages and reserves the complete output
budget plus a margin. No source is silently truncated. A context-limit failure
currently stops the offline run; future product clarification UX is not present.

Outputs:

- `preflight.json`: static prerequisite outcomes; never grants rollout readiness.
- `report.json`: identities, per-case timing/token counts, sampled system-memory
  peak and error/status codes, without question/source/draft text.
- `samples.private.json`: questions, source excerpts, unverified drafts and
  verifier results, **only for local manual comparison**. These are deliberate
  private study artifacts, not application logs or safe answers to publish.
- `review.template.json`: null manual grades and `user_accepted: false`.

The verifier checks each claim, conditions, missing parts, conflicts and status.
Code validates that citations exist and quotes occur in the returned source.
Neither exact quotes nor same-model verification proves entailment or complete
coverage. Failed drafts remain visible only in the private study artifact.

## Manual review and decision gate

Copy the review template to a new local review file. Compare samples with source
documents and fill boolean grades for every case: `correct_and_complete`,
`no_unsupported_claims`, `sources_valid`, `critical_passed`, and
`more_useful_than_excerpts`. The report SHA-256 binds the review to the run; the
report also records the private samples' SHA-256. Do not change either artifact
after review. The user must actually accept the reviewed examples before
`user_accepted` is set true; the tool/model must not invent that decision.

```powershell
.\env\Scripts\python.exe -m scripts.evaluate_answers assess --report .tmp/answer-study/run-01/report.json --review .tmp/answer-study/review.json --output .tmp/answer-study/decision.json
```

All gates must pass:

- Every published holdout answer is grounded and has valid sources.
- At least 90% of supported holdout cases receive a correct, complete answer;
  refusing answers does not improve this metric.
- All critical and adversarial cases pass source review.
- At least 95% of all measured cases finish successfully within 110 seconds.
- Sampled whole-machine memory stays at or below 85%; run alongside the normal
  Rita services to represent the deployment load. Sampling is not an OOM proof.
- At least 80% of supported holdout cases deliver an accepted answer judged more
  useful than excerpts, and the user accepts the comparison.

Exit codes: preflight uses 0 for static prerequisites met and 1 for blocked;
run uses 0 for completed-but-unreviewed, 1 for blocked prerequisites and 2 for
execution/input failures; assess uses 0 only when every gate passes, 1 when a
gate fails and 2 for invalid/incomplete review. No command adds or enables an API.

Only after the experiment and user acceptance pass should a subsequent change
add `/api/v1/answer`, authenticated `/chat-bot/answer`, progress SSE and portal
integration. Preserve retrieval while that work remains gated.

## Verification — 2026-11-13 working tree

The results below are historical implementation checks. See [current verification](../README.md#current-verification-status) for subsequent docs/test regression results. Synthetic batch-orchestration tests do not certify the installed generation runtime, real model quality or deployment resource use.

The fictional evaluator suite (26 tests) and direct retrieval/reranking/API
regressions passed together (125 tests). Initial sandbox runs
hit Windows temporary-directory permission errors; the successful run used a
repository-local `.tmp` test root outside that sandbox restriction. No real-model
quality or latency claim follows from these tests.

A read-only probe against the installed E5/reranker and configured database
returned `no_matches / relevance_unavailable`, with zero accepted excerpts.
This confirms the currently unavailable calibration, not absence of policy
content. Existing model-library deprecation warnings do not change that result.

Static preflight of the existing local environment reports a missing validated
retrieval profile, an incompatible historical case manifest, and no explicitly
provisioned generation runtime/model. Stage two remains blocked; the existing
40-case artifacts were preserved, not promoted to a new acceptance dataset.
