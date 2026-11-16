# Grounded answer evaluation and gated local API

The separate `/api/v1/answer` endpoint is implemented but **disabled by default**.
The filtered branch requires the offline study below and actual user acceptance
against the exact Qwen3.5-0.8B Q5_K_M model/runtime/code/retrieval configuration.
The explicitly unfiltered branch can run without a profile or study, retaining
unassessed relevance metadata, technical model checks, source validation and verifier.
Public HTTP/CLI retrieval remains excerpt-only. Backend/portal integration and
conversation history are outside this implementation.

`scripts.evaluate_answers` compares the same accepted evidence in three forms:
retrieval excerpts, an unverified generated draft, and the draft after code and
model verification. It never bypasses public relevance filtering. The historical
40-question answer/retrieval experiments do not satisfy this experiment's gates.

## Prerequisites and stop conditions

1. Meet the existing [retrieval calibration gates](online-reranking.md#evaluation-and-rollout).
   A supported schema-v2/v3 per-passage profile must match the current model/pipeline and immutable eligible
   publication corpus. Missing or stale assessment stops the experiment before
   generation. Do not synthesize passage labels from scores or lower the gates.
2. Prepare 60 source-reviewed Vietnamese cases before examining model output.
   The distribution is 20 supported, 10 partial, 10 absent, 10 ambiguous/personal,
   and 10 adversarial/exception/conflict cases. Each category is divided equally
   between development and holdout. Paraphrases share an intent group and never
   cross splits. Freeze the cases and prompt before the full run; do not tune on
   inspected holdout results. An inspected holdout needs replacement before a new
   acceptance claim.
3. Install optional `requirements-answer.txt` alongside the existing retrieval
   dependencies, and provision the local Qwen3.5-0.8B Q5_K_M GGUF. Record the
   conversion source/revision and model SHA-256; do not infer authenticity from
   the filename or metadata. No command downloads weights or changes `.env`.

## Native Python runtime

`llama-cpp-python==0.3.37` and `jinja2==3.1.6` are pinned in
[requirements-answer.txt](../requirements-answer.txt). The supported runtime
loads GGUF directly in the FastAPI Python process, on one dedicated executor
thread. It does not use Ollama, llama-server, an internal HTTP client or an
additional answer port. Imports and static preflight do not load model weights.

Install with `python -m pip install -r requirements-answer.txt`. When a compatible
CPU wheel is unavailable on Windows, source installation requires Visual Studio
C++ Build Tools; pip's isolated build provisions its declared CMake/build tools.
The tested Python 3.14.10 CPU build used Visual Studio Build Tools 2026, the
PyPI 0.3.37 source archive, and CUDA/Vulkan/BLAS disabled. A reproducible invocation
from an activated environment with those prerequisites is:

```powershell
$env:CMAKE_BUILD_PARALLEL_LEVEL = "6"
python -m pip install -r requirements-answer.txt --config-settings 'cmake.args=-DGGML_CUDA=OFF;-DGGML_VULKAN=OFF;-DGGML_BLAS=OFF'
```

Windows extraction/build paths must remain short enough for the compiler tools;
use a short ignored directory under `.tmp` when building from an explicitly
verified source archive. Do not patch third-party source or bypass dependency
resolution. Wheel/build hashes and the official source archive SHA-256 belong
in private provisioning notes. Host-native CPU wheels are not assumed portable
to different CPUs. No Python downgrade or server fallback is automatic.

The reference machine remains an Intel i5-10505 (6 physical/12 logical cores),
approximately 16 GiB RAM. The runtime uses six CPU threads, no GPU layers, an
8,192-token context, thinking disabled, 1,024 generation tokens and 512 verifier
tokens. It performs one generation and at most one verification call, without
repair. Actual compatibility and cooperative cancellation must pass a fictional
smoke test before a real answer study.

The model's own GGUF tokenizer/chat template is rendered once with
`enable_thinking=false` in a sandbox. Counting and inference use the same token
IDs; the root `models` tokenizer files belong to the reranker and are not used
for Qwen. A JSON grammar constrains each completion, followed by strict schema,
quote and model-verdict validation. The native grammar adapter omits string
`maxLength` to avoid the binding converter's excessive recursion at 2,000 characters;
Pydantic still enforces the original length limits before a draft can be published. Model state is reset between calls.

Runtime identity binds the binding version, Python/ABI, Jinja/NumPy versions,
binding source/native-library hashes, template hash and model hash. An explicit
`LLAMA_CPP_LIB_PATH` override is rejected. Exact bytes/configuration must match
the reviewed report; a version string alone is not sufficient. See the official
[binding documentation](https://llama-cpp-python.readthedocs.io/en/latest/) for
the underlying local GGUF interface.

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
`requirements-example.txt` already installed; a full run also needs
`requirements-answer.txt`. Preflight requires no database
connection or model inference:

```powershell
.\env\Scripts\python.exe -m scripts.evaluate_answers preflight --cases .tmp/answer-study/cases.json --output .tmp/answer-study/preflight-01
```

Without model arguments, preflight reports missing model configuration. An
absent/incompatible installed binding is reported separately without loading weights.
For a full run, replace every placeholder with an explicitly provisioned file
and its verified hash:

```powershell
.\env\Scripts\python.exe -m scripts.evaluate_answers run --cases .tmp/answer-study/cases.json --model models/Qwen_Qwen3.5-0.8B-Q5_K_M.gguf --model-sha256 <model-sha256> --output .tmp/answer-study/run-01
```

The output directory must not exist. There is no runtime executable or port
argument. Retrieval settings come from the existing explicit configuration
loader, including its semantic/intent settings. Retired `--runtime`,
`--runtime-sha256` and `--port` arguments are rejected by argparse.

The full run includes a fictional warm-up, then all 60 frozen cases. A 30-second
deadline covers each question's retrieval, generation and verification. On an
operational failure/timeout, the experiment stops, requests cooperative cancellation and drains its native
worker before closing the model; no later case starts during cleanup.
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
- At least 95% of all measured cases finish successfully within 30 seconds.
- Sampled whole-machine memory stays at or below 85%; run alongside the normal
  Rita services to represent the deployment load. Sampling is not an OOM proof.
- At least 80% of supported holdout cases deliver an accepted answer judged more
  useful than excerpts, and the user accepts the comparison.

Exit codes: preflight uses 0 for static prerequisites met and 1 for blocked;
run uses 0 for completed-but-unreviewed, 1 for blocked prerequisites and 2 for
execution/input failures; assess uses 0 only when every gate passes, 1 when a
gate fails and 2 for invalid/incomplete review. No command adds or enables an API.

Only after the experiment and user acceptance pass may filtered
`/api/v1/answer` be enabled. Explicit unfiltered trials are the separate exception
below; they do not produce official acceptance evidence. Authenticated `/chat-bot/answer`, progress SSE and
portal integration remain separate future work. Preserve retrieval independently.

## Local answer API

Run the existing `python -m src.cli.serve_api`. With no answer settings, retrieval
starts normally and `POST /api/v1/answer` returns 503 `answer_unavailable`.
Copy the documented names from [the environment example](../.env.example);
application entrypoints do not edit `.env` or restart an existing service.
Settings are read at startup; apply changes through an explicit service restart.

For filtered answering after a successful Q5 study and real manual acceptance, configure:

```dotenv
ANSWER_ENABLED=true
RELEVANCE_FILTER_ENABLED=true
ANSWER_MODEL_PATH=models/Qwen_Qwen3.5-0.8B-Q5_K_M.gguf
ANSWER_MODEL_SHA256=<verified-model-sha256>
ANSWER_REPORT_PATH=.tmp/answer-study/run-01/report.json
ANSWER_REVIEW_PATH=.tmp/answer-study/review.json
```

This filtered configuration also requires enabled reranking and a compatible
relevance profile; see [relevance setup](online-reranking.md#local-model-and-configuration).
These are placeholders, not supplied acceptance artifacts. In this filtered branch, keep the report,
review and report-adjacent `samples.private.json` local and private. Filtered startup
recomputes all manual gates and checks their hashes, report schema v3,
model/native-binding identity, implementation hash, retrieval settings/artifact hashes,
and the measured configuration. A standalone `integration_ready=true` flag is
insufficient. Any code/model/pipeline change invalidates the study; each request
also checks the corpus fingerprint. Never set acceptance on the user's behalf.
Report schema v1/v2, server-runtime reports and Q4/4B study artifacts cannot
authorize filtered answering. Retired `ANSWER_PORT`, `ANSWER_RUNTIME_PATH` and
`ANSWER_RUNTIME_SHA256` environment settings are ignored without editing `.env`.

### Explicit unfiltered answering

To generate from top-K without calibrated threshold filtering:

```dotenv
ANSWER_ENABLED=true
RELEVANCE_FILTER_ENABLED=false
ANSWER_MODEL_PATH=models/Qwen_Qwen3.5-0.8B-Q5_K_M.gguf
ANSWER_MODEL_SHA256=<verified-model-sha256>
```

This branch ignores answer report/review paths, including missing or stale files,
and does not require a relevance profile. The model hash, metadata, pinned binding
and warm-up remain mandatory. `RERANK_ENABLED=true` reranks candidates; false
uses vector order. Access/eligibility/effective-date checks and top-K bounds
remain enforced. There is no request-level bypass flag or automatic mode switch.

The pipeline's internal unassessed permission defaults to false; only the HTTP
answer service derives it from server configuration. Candidates must carry
`relevance.status=unassessed` plus `relevance_filter_disabled`; empty outcomes use
`no_candidates` plus that warning. Missing/contradictory metadata is rejected
before model inference. Sources are still consolidated and checked against the
question, with one draft and at most one verifier call. No answer is forced.

Official `scripts.evaluate_answers` remains filtered-only and rejects this
configuration before generation. Trials do not satisfy calibration/60-case
acceptance, cannot mark themselves user-accepted, and cannot issue an accepted
profile. Existing filtered reports are not rewritten to fit changed code.

`POST /api/v1/answer` accepts `{"text":"Fictional policy question?","top_k":5}`.
Validation matches retrieval (trimmed nonempty text, at most 8,000 characters
and the E5 token limit; integer `top_k` 1-20). The answer default is always five,
independently of `RETRIEVAL_TOP_K`; the acceptance study measures that default.
Larger contexts still must fit the full prompt/output budget without truncation.

The response includes:

- `status`: `answered`, `partial`, `needs_clarification`,
  `insufficient_evidence` or `unsupported`.
- `answer`: deterministic Vietnamese rendering of checked claim text with
  `[S1]` references, followed by checked limitations/clarification text.
- `claims`: checked text with `source_id` and verbatim supporting `quote`.
- `sources`: server-owned title/applicability/version/effective dates and origins
  (chunk/document/revision/run IDs, relative path and source spans). Exact duplicate
  excerpts consolidate only with identical title and applicability/version/dates;
  all corresponding origins remain. Source content is not repeated in metadata.
- `relevance`: server-owned retrieval status/warnings, preserved even after a
  verifier pass or rejected draft. Unfiltered candidates remain `unassessed` with
  `relevance_filter_disabled`; all HTTP 200 answer responses retain `evidence_completeness_not_assessed`.
  This additive field does not change the existing request or status/error codes.
- `limitations`, nullable `fallback_reason`, and retrieval/generation/verification/
  total seconds in `timings`. No confidence, chain-of-thought or failed draft.

Valid empty/partial/clarification outcomes return HTTP 200. Empty accepted evidence
skips inference. Citation/schema or verifier rejection returns a fixed insufficient-
evidence response without the rejected text. Missing/stale calibration or report-bound corpus changes in the filtered branch,
disabled/unavailable model, context overflow or runtime failure returns
503; invalid input returns 422; concurrent answer/retrieval returns 429; deadline
expiry returns 504. There is no automatic inference retry or repair loop.

The answer model is loaded once after technical checks (and filtered acceptance
checks when applicable), warmed with fictional
input, and owned by one executor thread. Tokenization, grammar, inference,
reset and release run on that same thread; the event loop stays responsive.
Thinking prefixes, reasoning-bearing text and incomplete output are rejected.
Startup failures leave retrieval available.

Timeout/disconnect uses a thread-safe cancellation flag, token stopping criteria
and the CPU native abort callback. The busy slot remains held through repeated
coroutine cancellation until native work exits. The context is never freed or
reused concurrently with decode. After timeout/uncertain inference failure,
answering stays unavailable until restart; restart revalidates technical checks
and the configured branch's prerequisites.

A 30-second deadline requests cancellation; cleanup and HTTP 504/shutdown may
exceed it. An in-process native thread cannot be force-killed safely. If native
code hangs without observing its callback, the busy slot and shutdown wait for
it; operators must restart the application process. A fatal native crash can
also affect FastAPI. This is the accepted tradeoff for in-process loading;
there is no hidden process/server fallback. Successful native cleanup releases
the slot so retrieval remains usable.

Logs contain fixed outcome/error codes and timings only, never questions,
source passages or drafts. Offline private samples deliberately retain these
for source review; the metadata report excludes source/draft/verdict text.

## Implementation validation

Synthetic tests cover source preservation/consolidation, acceptance binding,
rejected drafts, HTTP payloads, busy handling, timeout, repeated cancellation,
client disconnect and native thread/model lifecycle. Tests use fictional binding
boundaries, not real weights. Real native smoke evidence is reported separately
and does not establish Vietnamese policy quality or the 30-second target. See the fixed
[verification status](../README.md#current-verification-status) for actual runs.
The following older observations are historical and are not a current Q5 probe.

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
