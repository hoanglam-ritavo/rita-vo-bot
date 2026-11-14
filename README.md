# Rita Bot

Implemented: Python database foundation, agent-authored multi-format chunk packages, structural validation, transactional PostgreSQL publication, public-document retrieval through a function/CLI and local FastAPI, and a local gRPC server with health checking only. E5-small runs directly in Python; the system returns source chunks, not generated answers or aggregate conclusions. Chatbot generation, business RPCs, authenticated Type B tools and conversation history remain planned.

Source documents remain in `assets/`. Git-trackable current packages live at `chunks/<document_id>/`, containing `metadata.json`, `extraction.json`, `chunks.json` and an unchanged source snapshot. Agents author transcription and semantic chunks; Python initializes packages, manages revisions, validates, embeds with an explicitly supplied local model and publishes. There is no approval step or reviewer identity. Validation checks consistency, not transcription accuracy or business correctness.

Each source has at most one current package. Editing chunks keeps the source revision; changed source bytes require `python -m src.cli.prepare_documents revise --package chunks/<document_id> --source <relative-source-path> --supersedes-current`. Revision identity stays in metadata while the directory remains stable. Git preserves committed package history; `.gitattributes` preserves exact bytes across checkouts. Each environment still needs its own local model and explicit publication to its own database. See [layout migration and deployment](docs/offline-indexing.md#layout-migration-and-deployment) before using an older checkout's packages.

See the [indexing guide](docs/offline-indexing.md) for schemas, commands, metadata requirements and explicit non-destructive conversion of existing review/approved packages. Historical `indexing_chunks/` is untouched and never scanned. Model environments and authorization to process internal content must be established before agent reading; no hosted processing is implied.

The Offline Indexing Pipeline has two scopes. Scope 1 prepares sources and authors a complete package, with manual source/coverage checks and structural validation; no embedding model or database is required. Scope 2 reads that package, checks the actual tokenizer/model through preflight, embeds and publishes to PostgreSQL. Follow the [Scope 1 authoring procedure](docs/offline-indexing.md#agent-authoring-procedure) for focused detail chunks, source-backed inventories and optional groups within schema v2, or explicit evidence structures in schema v3. See [structured evidence](docs/structured-evidence.md). Author-declared coverage requires source review.

Before publication, use `python -m src.cli.preflight_documents --package <package-path> --model-path <local-model-directory> --model-revision <40-hex-commit> --json`. Install the optional local runtime from `requirements-example.txt` first. Preflight loads the local model and checks every chunk's token length without encoding by default; add `--check-embeddings` to generate and validate vectors in memory. It needs no database, accepts structurally valid hold/unknown packages, and never changes metadata or package files. Publication metadata blockers are reported separately. Passing preflight is not permission to publish or evidence of transcription/retrieval quality. Publication still performs its own checks; its `--dry-run` does not tokenize or embed. See the [preflight details](docs/offline-indexing.md#technical-preflight) and [Scope 2 operational guide](docs/offline-indexing.md#scope-2-package-reading-embedding-and-publication) for data mapping, outcomes and verification. Preflight vectors are discarded; publication encodes again unless an identical active configuration is skipped.

Entrypoints are in `src/cli/`, package processing in `src/ingestion/`, metadata validation in `src/catalog/`, database code in `src/database/`, tests in `src/tests/` and manual utilities in `scripts/`. `main.py` remains a thin database-check dispatcher. See the [application command reference](src/cli/README.md) and [manual utility reference](scripts/README.md) for command selection, status, prerequisites and effects. Source rename manifests, journals and `rename-history/` remain historical evidence.

Migration `0003_agent_packages` removes revision review state and renames review notes to evidence notes, preserving chunks, vectors and successful run history. Stop ingestion and run migrations explicitly before using the new publication code; conversion of local packages is separate and never changes the database.

See [ARCHITECTURE.md](ARCHITECTURE.md) and [AGENTS.md](AGENTS.md).

Offline corpus preparation on 2026-11-13 covers 28 PDFs / 334 physical pages, including twelve software manuals and the labor rules. See [package coverage and verification](docs/chunk-coverage-verification.md) for authoring changes, local-model evidence, held metadata and publication limits. Prepared packages are not automatically available through the API; database publication and corpus-matched relevance calibration remain separate operations.

Subsequent PostgreSQL publication on 2026-11-13 verified 13 eligible packages / 324 active vectors: 12 identical-run skips and one new benefits run (29 vectors). At that publication snapshot, fifteen packages / 296 chunks remained held; this is historical, not the current on-disk eligibility inventory. Real-database regression retrieval found required top-5 evidence for 51/53 supported evaluated cases; 77 cases requiring held sources were deferred. See [publication checks and limits](docs/chunk-coverage-verification.md#postgresql-publication--2026-11-13). No relevance profile or API rollout was performed.

## Current verification status

### Command documentation and shared runners - 2026-11-14

Post-edit verification of the working tree based on `67cb66d`: **580 passed, 1 skipped, 34 deselected** in the full non-integration suite; **246 passed** in the focused retrieval/IDE/preflight/evaluator/package/intent suites. Both successful runs reported the existing Starlette/AnyIO deprecation warning. This task added nine regression cases for shared-runner delegation and model revision validation, while retaining the existing timing/error/cleanup and entrypoint assertions. Existing and concurrently appearing configuration/model-path/API test/document changes were preserved; full-suite results describe the combined working tree, not this refactor alone.

The [application command reference](src/cli/README.md) and [manual utility reference](scripts/README.md) cover every direct Python/PowerShell file in their folders, including support modules, historical tools and retired stubs. Static checks verified inventory coverage, local links/heading anchors, whitespace, and side-effect-free imports of both shared modules. `--help` passed for 14 argparse entrypoints without executing their operations. Shared retrieval and model revision handling preserve existing command paths, result shapes and argparse diagnostics.

Two sandbox attempts failed while creating/accessing pytest temporary fixtures (WinError 3/5); those are environment failures, not passing checks. Successful runs used distinct new extended-length `.tmp/cli-docs-refactor` paths outside the sandbox, without changing permissions or deleting existing temporary trees. Full command (choose a new unused temporary path when repeating):

```powershell
.\env\Scripts\python.exe -m pytest -m 'not integration' -q --tb=short --basetemp '\\?\D:\sources\rita-id\rita-vo-bot\.tmp\cli-docs-refactor\full-01' -o cache_dir=.tmp/cli-docs-refactor/full-01-cache
```

No integration DB tests, migrations, publication, asset migration, model training, real-model benchmark, live API check or configuration rollout ran in this task. The checks establish synthetic regression compatibility and documentation structure, not live corpus quality or model acceptance.

### Earlier verification snapshots

The separately authorized clear-content publication on 2026-11-13 added **200 vectors from fifteen formerly held packages**. PostgreSQL read-back verified **28 current active publications / 524 active chunks** and preservation of all pre-existing bot-table rows. All fifteen repeated publications skipped as `identical_active_run`. Real local E5 preflight passed for the selected 200 chunks; focused package/publication/preflight suites passed **106 tests, 1 deselected**. No relevance profile or API restart was performed; public retrieval can still return `relevance_unavailable`. See [publication evidence, exclusions and limits](docs/chunk-coverage-verification.md#clear-content-publication-follow-up--2026-11-13). The documentation/test reconciliation below is separate verification with its own limits.

Documentation/test reconciliation of the 2026-11-13 working tree based on `e656991`, including existing uncommitted runtime, intent, answer-evaluation and corpus changes. Those results superseded earlier test counts at that time; the command documentation/refactor entry above now provides the latest suite results. Historical verification entries remain snapshots of their own tasks.

Static inspection at the start of this task found 28 packages / 524 chunks, all with `include/active` metadata (27 schema v2, one v3). This differs from the earlier preparation/publication snapshots. No database publication, source fidelity, metadata authorization or calibration compatibility is established by these counts. The 600-case intent draft remains `reviewed: false`. See [corpus history and current boundary](docs/chunk-coverage-verification.md#current-working-tree-boundary).

Post-edit validation: **569 passed, 1 skipped, 34 deselected**, with the existing Starlette/AnyIO deprecation warning, using the full non-integration suite. The four focused suites passed **73 tests before test edits**, then **102 tests** after evaluator/answer/intent additions; three further public empty-outcome cases were included in the final full run. The task adds 32 parametrized regression cases without removing or skipping existing assertions.

Initial sandbox attempts could not create/access pytest temporary fixtures (WinError 5); they are environment failures, not passing validation. Successful runs used fresh extended-length `.tmp/docs-test-reconciliation` directories outside the sandbox, with separate pytest cache directories. No permissions were changed and no existing temporary trees were deleted. Final command (use a new unused temporary path when repeating):

```powershell
.\env\Scripts\python.exe -m pytest -m 'not integration' -q --tb=short --basetemp '\\?\D:\sources\rita-id\rita-vo-bot\.tmp\docs-test-reconciliation\full-01' -o cache_dir=.tmp/docs-test-reconciliation/full-01-cache
```

Static checks covered local links/heading anchors in 14 Markdown files, documented local command module paths, intent example configuration fields and changed-file whitespace. The new synthetic tests exercise filtered evaluator grading/drift/cleanup, the full answer batch with mocked model/storage boundaries, intent promotion rejection on corpus drift or cleanup failure, and distinct public empty outcomes without candidate disclosure. They do not validate real model/runtime compatibility or semantic source accuracy.

No integration database tests, publication, migrations, live API/model checks, intent training, real-model benchmark or application rollout ran in this task. Existing working-tree runtime/data changes were preserved. Known evaluator report limitations remain separate implementation work, documented in [retrieval evaluation](docs/retrieval-evaluation.md#known-evaluator-reporting-gap).

## Setup (PowerShell)

Run from the project root with Python 3.14 installed. Create the virtual environment if absent:

```powershell
if (-not (Test-Path -LiteralPath .\env)) {
    py -3.14 -m venv env
}
.\env\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item .env.example .env
}
```

The annotated [environment example](.env.example) groups database, transport, model and retrieval settings, with value limits and provisioning requirements. Preserve local values when updating comments; process environment variables override `.env`, and running services need a restart to load changes. `RERANK_PROFILE_PATH` is a commented example, not a supplied artifact: provision a validated corpus-matched profile through the [reranking workflow](docs/online-reranking.md) before expecting accepted excerpts.

Configuration presentation verification (2026-11-13, existing working tree): parsed `.env` values were identical before and after the comment/layout update; `.env.example` only removed the active sample profile assignment by commenting it out. Parser checks found no malformed entries or duplicate active keys. No API, database, model benchmark or application test suite was run for this documentation-only change.

Fill in `DATABASE_URL` in `.env` before running:

```powershell
python -m alembic upgrade head
python -m src.cli.check_db
```

PostgreSQL must already have the target database and the pgvector extension installed on the server. Installing the Python `pgvector` package does not install the server extension. If the migration account cannot enable it, ask the DBA to run `CREATE EXTENSION IF NOT EXISTS vector;` in the target database before migration.

## Local question retrieval

Install `requirements-example.txt` for the local embedding runtime. Call the retrieval CLI with an existing local model directory and its declared full commit:

```powershell
python -m src.cli.retrieve_documents --question "What benefits are available?" --model-path C:/models/multilingual-e5-small --model-revision <40-hex-commit> --top-k 5 --json
```

Replace the model placeholders before execution. The command returns source chunks, not an LLM answer. `--top-k` defaults to 5 (range 1–20). Questions must be nonempty strings with at most 8,000 characters after trimming and fit the model token limit including `query: ` and special tokens; no truncation is performed. The local model is loaded without downloads or remote code. Match output includes content, source spans, document/revision/run IDs, title, relative source path, applicability, effective dates and cosine distance. Smaller distances order internal vector candidates; distances are not confidence scores. Opt-in CPU reranking changes ordering; see [online reranking](docs/online-reranking.md). Results require a matching per-passage calibration profile. Without one, candidates are withheld with `fallback_reason: relevance_unavailable`; top-K is only an upper bound.

Only current, include/active, effective public revisions with active successful runs are searched. Runtime model name, revision, file fingerprint, normalization, dimension and passage prefix must match the indexed run. Private documents cannot be enabled through a CLI argument. Exact cosine search needs no new vector index. Database transactions are read-only; no package or vector data is modified. Statuses distinguish `matches_found`, `no_eligible_documents`, `model_mismatch` and `no_matches`; operational failures are errors, never empty evidence. Exit codes are 0 for valid results including empty results, 1 for invalid input/model incompatibility and 2 for operational errors. There is no automatic file logging of questions or retrieved text.

To avoid passing Vietnamese text through shell arguments, open `scripts/try_retrieval.py`, save it as UTF-8, and edit `QUESTION`, `TOP_K`, `MODEL_PATH` and `MODEL_REVISION` at the top. Select this repository's `env/Scripts/python.exe` interpreter in your IDE, then run the file. Its default model path points to the locally prepared pinned model under `models/embeddings`; that model directory and PostgreSQL configured in `.env` must still be available. Direct file execution works from any working directory; imports and model paths are anchored to the repository, without changing the working directory. Alternatively, from the repository root run:

```powershell
.\env\Scripts\python.exe -m scripts.try_retrieval
```

The script uses the same filtered retrieval contract and displays accepted chunks (up to `TOP_K`); its default vector-only mode withholds candidates with `relevance_unavailable`. Accepted matches preserve content and source locations; the compact script currently shows a generic no-match message, so inspect CLI JSON for the fallback reason. `QUESTION` stays user-owned; do not edit it for batch evaluation. It does not start the API or write the database. No separate model server is required. Accepted excerpts are not synthesized answers or a complete inventory.

The script ends with two timings: model initialization (including fingerprinting and loading inside `service.start()`) and retrieval after the model is ready (query embedding, PostgreSQL search and result construction inside `service.retrieve()`). Measurements use `time.perf_counter()` and exclude input validation, service construction, shutdown, formatting and terminal output. CLI JSON includes unrounded seconds in `timings.model_initialization_seconds` and `timings.retrieval_seconds`; the script displays seconds to three decimal places. A failed or unstarted step has `null`, displayed as an unavailable completed measurement, not zero. Completed measurements survive later retrieval/shutdown errors. Each script invocation loads the model again, and the first retrieval may include database connection setup and first-execution overhead. These are observations of that invocation, not a stable benchmark of production latency; no warm-up or extra query is performed.

For repeated calls, reuse a service instance on one event loop:

```python
from src.retrieval import RetrievalService
from src.database import close_database

async def retrieve_example(model_path, model_revision):
    service = RetrievalService(model_path=model_path, model_revision=model_revision)
    try:
        await service.start()
        return await service.retrieve("What benefits are available?", top_k=5)
    finally:
        await service.close()
        await close_database()  # Application owner closes the shared database at shutdown.
```

The application owns the loop; use the existing `run_async()` wrapper only at a synchronous Windows entrypoint, never inside another event loop. Model operations run in a worker thread and are serialized per service. Model files must remain immutable until the service closes; restart with a new service after changing models. Service close does not dispose the shared database. Imports do not load configuration, models or connect. Both the retrieval CLI and the HTTP API below return source chunks only. Conversation history, personnel lookup and chat RPCs remain unimplemented.

## Local retrieval API and Swagger

Install `requirements-dev.txt` and `requirements-example.txt` in the Python 3.14 repository virtual environment. E5-small is loaded directly in Python on CPU; no separate inference service or generative model is used. The application never downloads weights or falls back to hosted inference.

Use `.env.example` for `API_HOST`, `API_PORT`, `E5_MODEL_PATH`, `E5_MODEL_REVISION` and `RETRIEVAL_TOP_K` (default 5, range 1-20). Existing environment variables override repository `.env`. Relative model paths resolve from the repository root. The default pinned E5 directory under `models/embeddings` must exist and match the indexed run. Model files must remain immutable until shutdown. PostgreSQL, an explicitly migrated schema and already-published public documents are prerequisites; startup does not migrate or publish.

```powershell
.\env\Scripts\python.exe -m src.cli.serve_api
```

Open http://127.0.0.1:8000/docs and call `POST /api/v1/retrieve`:

```json
{"text":"What eligibility conditions apply?","top_k":5}
```

`text` must be a nonempty string after trimming, at most 8,000 characters and within E5's token limit including the query prefix; input is not truncated. Optional `top_k` must be an integer from 1 to 20; omitting it uses `RETRIEVAL_TOP_K`. Null, boolean, string and out-of-range values are rejected, as are extra fields.

The response contains `status` (`matches_found`, `no_eligible_documents`, `no_matches`), `matches`, required nullable `fallback_reason`, `relevance` and `timings` (`retrieval_seconds`, `total_seconds`, `rerank_seconds`). Each match retains content, chunk/index/run/document/revision IDs, title, relative source path, version label, applicability, effective dates, source spans, author ID/role and cosine distance. No vectors, generated answer, confidence or evidence-completeness assessment are returned. Additive relevance metadata and optional raw reranker scores are described in [online reranking](docs/online-reranking.md). Internal vector candidates sort by smaller distances; enabled reranking sorts them by raw reranker scores before relevance filtering. Neither score is confidence. Only passages passing a matching calibrated threshold are returned; missing assessment produces an explicit empty fallback. The API cannot count benefits, resolve personal eligibility or determine whether top-K covers an exhaustive request.

This is a breaking replacement: `POST /api/v1/ask` no longer exists and returns 404. Clients must use the new route and response. Old `ANSWER_TOP_K` and `OLLAMA_*` settings are unused; configure `RETRIEVAL_TOP_K` instead. Removing the project dependency does not uninstall software or delete model weights on the machine.

Use the supplied entrypoint: it binds only to loopback (default `127.0.0.1:8000`), without authentication, with one worker and no reload on the psycopg-compatible Windows loop. Startup loads/fingerprints E5 once on CPU and checks the database. Startup logs separate model initialization and total startup durations. One retrieval runs at a time; additional requests receive 429. The overall deadline is 120 seconds. Cancellation waits for an in-progress embedding worker to finish safely before releasing the busy state, so timeout responses and shutdown may exceed the nominal deadline.

`/health/live` reports HTTP availability. `/health/ready` checks initialized model state and database connectivity, not publication completeness, model compatibility with every stored run or semantic quality. Shutdown drains active work and closes E5, the optional reranker and the shared database. Imports create no runtime connections. Questions and document contents are not logged or retained as history.

Invalid input returns 422, busy 429, unavailable dependencies/model mismatch 503 and timeout 504. Empty valid retrieval returns 200; operational failures never become empty evidence. gRPC remains an independent health-only process. See [manual retrieval evaluation](docs/retrieval-evaluation.md) for read-only real-model checks.

## Optional online CPU reranking

See [online reranking](docs/online-reranking.md) for the pinned local ONNX model, tokenizer provisioning, configuration, response changes, read-only evaluation and rollout gates. This is disabled by default and requires no rechunking, embedding or database migration.

## Optional intent observation

The lightweight [intent classifier](docs/intent-classifier.md) reuses the E5 query vector with small logistic heads for source intent and Type A evidence needs. `INTENT_MODE=off` is the default; `observe` requires an explicitly provisioned, accepted local JSON artifact through `INTENT_ARTIFACT_PATH`. It only emits safe technical logs and does not change HTTP/CLI responses, passage filtering, or route requests to Type B. There is no additional transformer or embedding call. Missing or incompatible classifier artifacts leave retrieval available.

Install `requirements-intent.txt` only for offline training. `python -m scripts.train_intent --help` describes training/calibration; `python -m scripts.benchmark_intent --help` describes the paired read-only benchmark required before promotion. Follow the guide for reviewed private datasets, model identity, quality/latency gates and rollback. No command automatically changes `.env`, downloads models, migrates storage or publishes documents.

For Stage 2 batch operation, use `python -m src.cli.prepare_intents validate --config .tmp/intent-v1/config.json --json`, followed by `preflight` and then `run` with the same config. See [configuration and batch outputs](docs/intent-classifier.md#unified-offline-preparation-cli) and the [example](docs/examples/intent-preparation.json). Validation aggregates structural errors without loading ML; preflight tokenizes without encoding; run requires reviewed labels and writes a new manifest/report/candidate directory. No database is required. The old trainer uses the same shared implementation and remains supported; benchmark/promotion are separate.

### Stage 2 batch CLI verification — 2026-11-13

Working tree based on `e656991`, after adding the shared offline preparation CLI: **537 passed, 1 skipped, 34 deselected** in the full non-integration suite, with the existing Starlette/AnyIO deprecation warning. After the final run-directory ownership/cleanup adjustment, all **20 batch CLI tests passed** again. Tests used the isolated extended-length `.tmp/intent-test-runs` workspace outside the sandbox. Coverage includes no-ML imports/help, aggregated validation, review-before-model gating, token-only preflight, legacy/new artifact parity, unique run directories, atomic manifests, interruptions, changed inputs and failure reporting. `pip check`, legacy/new CLI help and scoped diff checks passed.

Actual CLI validation accepted the structure of the 600-case draft while reporting `review_required: true` and `training_allowed: false`. Real local CPU E5 preflight checked all 600 questions without encoding; none exceeded the token limit. The report is under `.tmp/intent-v1/preflight-cli-report.json`. An actual `run` invocation correctly stopped before model loading with `human_review_required`, leaving a diagnostic manifest and no candidate. No reviewed training, semantic quality evaluation, database benchmark, accepted artifact or observation enablement was performed. Existing dataset/chunk changes and `.env` were preserved.

### Intent verification — 2026-11-13

Implementation working tree based on `e656991`, preserving existing documentation, corpus and answer-evaluation changes. Full non-integration verification after implementation: **516 passed, 1 skipped, 34 deselected**, with one existing Starlette/AnyIO deprecation warning. After final dataset-validation/diagnostic hardening and one additional test, the focused intent suites passed **35 tests**. Tests used a separate extended-length `.tmp/intent-test-runs` directory outside the sandbox; the first sandbox attempt failed at pre-existing temporary-directory permissions. No permissions or existing temporary trees were changed. Synthetic tests cover NumPy/sklearn parity, training/holdout separation, acceptance gates, off/observe retrieval invariance, HTTP/CLI wiring, encode counts, cancellation and cleanup. CLI help and `pip check` passed.

The private draft `.tmp/intent-v1/draft-cases.json` contains 600 structurally checked, **unreviewed** cases; training rejects it before loading E5. Human label/split review, real-model intent training, the quality gate and paired retrieval latency benchmark have **not run**. No accepted intent artifact has been produced, and observation has not been enabled. Database integration tests were deselected; no migration, publication, source-package edit, backend or portal change is part of this implementation. See the guide for the draft's template/diversity limitations and the independent retrieval calibration prerequisite.

## Offline answer evaluation (endpoint not implemented)

The stage-one [answer evaluation tool](docs/answer-evaluation.md) compares accepted
retrieval excerpts with a locally generated draft and a separately checked draft.
It requires a valid retrieval calibration profile, frozen source-reviewed cases,
explicit local model/runtime artifacts, and manual review before any integration
decision. Public retrieval remains excerpt-only; there is no answer endpoint.
See the guide for commands, private artifact handling, gates and the current
2026-11-13 verification/blocked status. The historical local 40-case answer files
are not sufficient for this new experiment.

### Fallback verification - 2026-11-13

Implementation working tree, including pre-existing reranking changes: the public function/CLI/HTTP path now filters each passage before top-K and returns a required nullable `fallback_reason`. Uncalibrated vector-only/reranked candidates are withheld. See [the response and rollout contract](docs/online-reranking.md#retrieval-and-response-semantics). No source packages, publications, schema, model files or application `.env` were changed.

Non-integration verification: **454 passed, 1 skipped, 34 deselected** using an isolated extended-length `.tmp/` basetemp outside the Windows sandbox. Earlier sandbox runs hit access-denied temporary-directory errors; no permissions were changed. The new per-passage 100-question source-labeled benchmark has **not been completed**: the available historical 40-question set lacks per-candidate labels and has related intents across splits. No schema-v2 profile was emitted, and no real-model quality/latency gate is claimed. Subsequent focused checks passed: 62 retrieval/API tests after profile hardening, then 24 reranking/benchmark tests after adding two orchestration cases. The pinned local ONNX smoke passed on two fictional passages without database access. PostgreSQL integration tests were not executed; synthetic tests do not verify the new corpus snapshot query on a real database.

### Online reranking verification — 2026-11-13

Historical top-1 evaluation, predating per-passage filtering (not a current rollout approval). Executed on the implementation working tree with the downloaded ONNX verified against official revision `1427fd652930e4ba29e8149678df786c240d8825` and its matching local tokenizer. A read-only, package-pinned 40-question evaluation (30 supported, 10 unsupported; known prompts kept as regression) found complete top-5 evidence for all 30 supported cases, with no candidate-pool misses. The required bereavement-family top-1 case failed: its detail ranked fourth. The calibrated relevance gate also failed (holdout positive top-1 recall 75%; calibration positive top-1 recall 46.7%; no holdout negative acceptances at that threshold). No calibrated profile was emitted; reranking remains disabled by default.

On 100 sequential post-warm-up requests with 30 candidates and batch size 4, p50 was 2.59 s and p95 was 3.88 s. Peak process working set was 1,951,526,912 bytes (about 1.82 GiB), including E5 and ONNX. Host: Windows x86-64, Intel Family 6 Model 165, Python 3.14.10. These observations are not a deployment SLA or evidence of complete policy coverage. Selected packages/storage passed before/after read-only verification. Local reports/cases are under ignored `.tmp/online-evaluation/`; private questions and source contents are not committed. The benchmark preceded final profile-fingerprint hardening (batch/runtime identity), which does not change candidate selection or inference; its profile fingerprint is intentionally not reusable by the final implementation.

Final non-integration verification: `python -m pytest -m 'not integration' -q` with a fresh extended-length `.tmp/` basetemp on Windows: **442 passed, 1 skipped, 34 deselected**. An initial ordinary-path run encountered 17 legacy asset-restructuring fixture errors from Windows path length; those resolved with the documented extended-length path, without changing the fixtures. Focused retrieval/API tests and the real local ONNX smoke passed. The updated PostgreSQL integration cases were **not executed**: automatic approval review rejected migrations/writes against the configured test URL because disposable ownership had not been verified. Read-only evaluation does not replace those transaction/eligibility fixture tests. No publication, schema change or stored-vector update was performed.

## Retrieval evaluation

Use `python -m scripts.evaluate_retrieval --package chunks/<document_id> --cases .tmp/evaluation/cases.json --model-path <local-model-directory> --model-revision <40-hex-commit> --top-k 5 --output .tmp/evaluation/report.json` for read-only evaluation of a published package. Package and cases paths are required; top-K defaults to 5 (1–20). Reports may only be written under `.tmp/`, outside the package/model and without overwriting the case file. The evaluator reads revision identity from metadata and rejects stale package/model fingerprints, ineligible revisions, changed inputs or stored content/spans/vectors that differ from the selected package. It records ranks, distances, model-load/overall question timings and storage verification, without copying retrieved content or vectors into reports. These timings do not separate query embedding from database latency.

Author cases before examining retrieval rankings. A case file contains `revision_id`, `chunks_sha256` (the `canonical_hash` of the validated payload's `chunks` returned by `src.ingestion.document_packages.load_package`) and a nonempty `cases` array. Each case has a unique string `id`, `question`, `category` (`detail`, `condition`, `aggregate`, or `unsupported`), optional boolean `critical`, and `required_groups`: a list of nonempty lists of zero-based package chunk indices. Every group must have at least one retrieved member from the pinned revision/run. For example, `[[1, 2], [3]]` requires chunk 1 or 2 plus chunk 3. Choose chunks containing the required answer evidence, not merely a shared source segment; an inventory can reference a detail's segment without containing its amount. Unsupported cases may have empty groups and are marked for manual review, never scored as answered or correctly refused. Keep real-document case files and reports under ignored `.tmp/`; committed tests use synthetic content.

The target is at least 90% complete evidence among detail/condition cases with every marked supported critical case passing. Aggregate results are reported separately. Exit codes are 0 for meeting this target, 1 for missing it, and 2 for evaluation/input/storage failures (argument errors also use 2). An unsupported-only set cannot meet the target. This evaluates evidence retrieval, not generated answers or fallback behavior. Agent review must still assess ambiguous wording, missing operational facts and semantic sufficiency. Package changes require re-pinning cases and a matching publication; retain baseline reports and disclose any tuning on the evaluation questions.

## Local gRPC server

After installing the dependencies, run `python -m src.cli.serve_grpc` from the repository root. This command does not require database configuration, migrations or a model. The intended inbound direction is the monolithic backend calling this chatbot microservice; business RPC contracts are not implemented yet.

| Variable | Default | Accepted values |
| --- | --- | --- |
| `GRPC_HOST` | `127.0.0.1` | Only `127.0.0.1` |
| `GRPC_PORT` | `50051` | Integer from 1 to 65535 |
| `GRPC_SHUTDOWN_GRACE_SECONDS` | `5` | Finite, non-negative seconds |

Configuration loads explicitly from the repository-root `.env`; environment variables take precedence. Imports do not load configuration or open sockets. Invalid configuration or an occupied port fails startup with exit code 1, without selecting another port. Ctrl+C stops the server gracefully and exits with 0; shutdown failures exit with 1.

From a second PowerShell terminal with the virtual environment activated, call the standard health RPC:

```powershell
@'
import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

with grpc.insecure_channel('127.0.0.1:50051') as channel:
    response = health_pb2_grpc.HealthStub(channel).Check(
        health_pb2.HealthCheckRequest(service=''), timeout=3,
    )
    print(health_pb2.HealthCheckResponse.ServingStatus.Name(response.status))
'@ | python -
```

Use your configured port if changed. `SERVING` confirms only the gRPC transport; it does not check the database, model, retrieval or chatbot readiness. Unknown service names return `NOT_FOUND`. Shutdown marks health `NOT_SERVING` and allows in-flight RPCs up to the configured grace period.

This development server uses plaintext loopback only, without authentication or TLS. It cannot accept calls from another machine or a separate container network namespace. Remote backend connectivity, authentication, TLS/mTLS and chatbot contracts remain future work. The pinned gRPC packages are checked on Windows with Python 3.14 and the existing psycopg-compatible `SelectorEventLoop` runner. `main.py` retains its database-check behavior.

## Database configuration and behavior

`DATABASE_URL` uses `postgresql+psycopg://user:password@host:5432/database`. URL-encode special characters in usernames and passwords. Configure TLS for the server, for example `?sslmode=verify-full&sslrootcert=C%3A%2Fcerts%2Froot.crt`. System environment variables take precedence over `.env`. Do not commit `.env` or credentials.

`MIGRATION_DATABASE_URL` optionally supplies an account with DDL permissions; when absent or empty, migrations use `DATABASE_URL`. Migrations run only through explicit Alembic commands, not at application startup. The current downgrade preserves the shared `vector` extension. Future runtime access should use permissions needed for application tables rather than migration privileges.

For example, the synthetic password `abc@123` must be written as `abc%40123` in a URL: `postgresql+psycopg://demo:abc%40123@localhost:5432/demo`. This changes only the URL representation, not the database password. Keep the separating `@` before the host; quoting the URL does not replace URL-encoding. The same rule applies to migration and test URLs. Configuration validation rejects detectable unencoded `@` in the connection authority with a credential-safe message; it does not automatically repair URLs or verify credentials/connectivity. Encode a literal `%` as `%25` to avoid unintended decoding.

From the project root, use `python -m src.cli.check_db` (preferred) or `python main.py` (wrapper). Both exit with 0 when connectivity and the extension check succeed, or 1 for configuration, connection, or extension errors. Both always dispose the engine. Importing either entrypoint does not load database configuration or establish a connection. Connection failures use generic messages to avoid exposing credentials; consult server logs for detailed diagnostics.

## Using the database in an async handler

This example demonstrates the database helper independently of the local FastAPI retrieval service.

```python
from sqlalchemy import text
from src.database import session_scope, close_database

async def handle_event():
    async with session_scope() as session:
        return await session.scalar(text('SELECT 1'))

# In the future bot/API shutdown hook:
# await close_database()
```

Each task must call `session_scope()` separately. The context commits on success, rolls back on failure, and closes the session; do not commit manually inside it. The engine is created lazily and shared within one process/event loop. Dispose it before closing that loop or switching to another. SQL echo is disabled by default.

On Windows, the entrypoint and Alembic use `SelectorEventLoop` through `src.database.runtime.run_async`. When integrating a framework, configure a psycopg-compatible loop at the framework-managed entrypoint. Do not call `run_async()` inside an already running event loop.

## Adding migrations

Future models must inherit `src.database.Base`, live in `src/database/models/`, and be exported by its `__init__.py`. Alembic imports this package in `src/database/migrations/env.py` to register all tables before metadata is configured. Generate a revision:

```powershell
python -m alembic revision --autogenerate -m "description"
```

Review the generated migration before applying it with `python -m alembic upgrade head`. The migration template supports `Vector`. Autogeneration skips reflected tables outside application metadata; intentional removal of an application table requires an explicit, reviewed drop migration.

## Testing

Run unit tests (shared database fixtures only execute when requested by integration tests):

```powershell
python -m pytest -m 'not integration'
```

The non-integration suite includes real loopback gRPC calls on ephemeral ports, without an external backend or database. Synthetic multi-format package/converter tests check JSON contracts and byte preservation; they do not parse actual PDF/XLSX files or verify agent extraction accuracy. Publication tests are in `src/tests/test_publication.py` and `src/tests/test_publication_integration.py`; the production module retains its existing name.

Pytest stores its cache in `.tmp/pytest-cache` and, by default, test temporary files in `.tmp/pytest-work`. The test configuration creates the temporary root when pytest starts and restores its environment override at shutdown; importing the fixtures does not create directories. Pytest manages numbered session directories and locks so concurrent runs use separate temporary directories. The retention policy keeps the latest three sessions after normal cleanup, including successful tests for debugging. Active, interrupted or permission-blocked sessions can remain longer; this is not a disk-space limit. The entire `.tmp/` workspace is Git-ignored.

Explicit `PYTEST_DEBUG_TEMPROOT` or `--basetemp` overrides are respected. Keep overrides under `.tmp/`; create their parent directories first. Prefer `PYTEST_DEBUG_TEMPROOT` when changing the temporary root while retaining pytest's session numbering and cleanup. Never share a fixed `--basetemp` between concurrent runs. Pytest may delete an existing basetemp directory before running, and explicit basetemp directories do not use the three-session retention policy. Never select a source or package directory.

On Windows, if temporary-directory permissions or long paths prevent pytest from running, use a fresh, writable directory under `.tmp/`. For example, run `New-Item -ItemType Directory -Force .tmp` and select an unused path such as `--basetemp '\\?\D:\sources\rita-id\rita-vo-bot\.tmp\pytest-work-retry-1'`, adjusted for your checkout. Extended-length paths can address long paths but do not bypass permissions. Errors are reported without silently choosing another temporary root. Do not create new `pytest-cache-*` directories at the repository root. Remove obsolete temporary directories only after checking their contents and confirming no test session is using them; leave inaccessible directories for explicit permission troubleshooting.

For integration tests, configure `TEST_DATABASE_URL` to a dedicated disposable PostgreSQL database with pgvector installed on its server, then run:

```powershell
python -m pytest -m integration
```

Integration tests modify the selected database. The fixture validates `TEST_DATABASE_URL` and rejects a match with the configured application or migration host/port/database before running migrations; it cannot detect different hostname aliases for the same server. It upgrades to head twice to check idempotence. Publication/migration tests then require all application tables to be empty; that check occurs **after** the migration, not before it.

Tests write synthetic documents, revisions, access rules, relations, ingestion runs and vector chunks into application tables and delete application-table rows during fixture cleanup. The migration preservation test also downgrades to `0002_type_a_catalog` and upgrades to head; removed review statuses cannot be recovered and downgrade initializes them as pending. Other tests create/drop randomly named tables or use temporary vector tables. Normal completion leaves application schemas, `alembic_version` and the vector extension in the disposable database. Never use production, the application database or a database containing data you need to keep.

Publication integration tests use synthetic embeddings and a synthetic local model directory to exercise PostgreSQL behavior, including rollback after metadata writes and at chunk insertion. They do not validate a real embedding model or tokenizer.

To verify both extension states, run the suite against a fresh test database where vector has not been enabled, then rerun with vector already enabled. Missing `TEST_DATABASE_URL` causes integration tests to be skipped; a skipped run does not verify real PostgreSQL connectivity. Do not substitute SQLite for these checks.

Historical source-utility compatibility tests are separate from v2 ingestion. The standalone PowerShell test is not collected by pytest and uses only synthetic temporary sources:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File src/tests/powershell/test-normalize-assets.ps1
```
