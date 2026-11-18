# Rita Bot

Implemented: Python database foundation, package-owned ingestion/publication and local FastAPI retrieval/answer services. E5-small and optional ONNX reranking return unassessed source candidates; template answers require exact eligible evidence. Optional trusted-backend Type B provides bounded work-profile evidence. Opt-in [durable conversations](docs/conversations.md) add message storage, structured follow-ups and cache/checkpoint recovery without a generative model. Broader operational tools and personal eligibility decisions remain unsupported.

An opt-in [readonly employee source](docs/employee-source.md) now supplies a separate
PostgreSQL connection, explicit 64-field employee_sso_store repository, immutable
contracts and service reads by user_id or employee_id (one or up to 200 IDs),
plus bounded one-hop manager resolution. Implementation is owned by
`src/type_b/employee_store/`; manager work summaries can include unlinked managers
without exposing private profiles through HTTP. Configure
`HRM_DATABASE_URL` with an externally provisioned SELECT-only role and optionally
`HRM_DATABASE_SCHEMA` (default public); it never uses the Type A URL or Alembic.
Run `python -m src.cli.check_employee_source` for a metadata-only preflight.
Every relation on this connection is readonly. The opt-in
[Type B HTTP adapter](docs/type-b-http.md) requires trusted hrm_user_id for retrieve/answer,
with TYPE_B_ENABLED=false by default and X-Rita-Service-Key required for employee
context. Backend must derive the reference from its authenticated user and enforce
access. Catalog requirements select relevant work, employment, manager and limited
task-specific fields. Unified evidence preserves policy/fact provenance without
deciding individual eligibility. See [current contract](docs/contextual-answers.md).

Source documents remain in `assets/`. Git-trackable current packages live at `chunks/<document_id>/`, containing `metadata.json`, `extraction.json`, `chunks.json` and an unchanged source snapshot. Agents author transcription and semantic chunks; Python initializes packages, manages revisions, validates, embeds with an explicitly supplied local model and publishes. There is no approval step or reviewer identity. Validation checks consistency, not transcription accuracy or business correctness.

Each source has at most one current package. Editing chunks keeps the source revision; changed source bytes require `python -m src.cli.prepare_documents revise --package chunks/<document_id> --source <relative-source-path> --supersedes-current`. Revision identity stays in metadata while the directory remains stable. Git preserves committed package history; `.gitattributes` preserves exact bytes across checkouts. Each environment still needs its own local model and explicit publication to its own database. See [layout migration and deployment](docs/offline-indexing.md#layout-migration-and-deployment) before using an older checkout's packages.

See the [indexing guide](docs/offline-indexing.md) for schemas, commands, metadata requirements and explicit non-destructive conversion of existing review/approved packages. Historical `indexing_chunks/` is untouched and never scanned. Model environments and authorization to process internal content must be established before agent reading; no hosted processing is implied.

The Offline Indexing Pipeline has two scopes. Scope 1 prepares sources and authors a complete package, with manual source/coverage checks and structural validation; no embedding model or database is required. Scope 2 reads that package, checks the actual tokenizer/model through preflight, embeds and publishes to PostgreSQL. Follow the [Scope 1 authoring procedure](docs/offline-indexing.md#agent-authoring-procedure) for focused detail chunks, source-backed inventories and optional groups within schema v2, or explicit evidence structures in schema v3. See [structured evidence](docs/structured-evidence.md). Author-declared coverage requires source review.

Before publication, use `python -m src.cli.preflight_documents --package <package-path> --model-path <local-model-directory> --model-revision <40-hex-commit> --json`. Install the optional local runtime from `requirements-example.txt` first. Preflight loads the local model and checks every chunk's token length without encoding by default; add `--check-embeddings` to generate and validate vectors in memory. It needs no database, accepts structurally valid hold/unknown packages, and never changes metadata or package files. Publication metadata blockers are reported separately. Passing preflight is not permission to publish or evidence of transcription/retrieval quality. Publication still performs its own checks; its `--dry-run` does not tokenize or embed. See the [preflight details](docs/offline-indexing.md#technical-preflight) and [Scope 2 operational guide](docs/offline-indexing.md#scope-2-package-reading-embedding-and-publication) for data mapping, outcomes and verification. Preflight vectors are discarded. Publication verifies stored data before skipping unchanged content; explicit metadata-only updates preserve the existing run/chunks. Changed publication reuses compatible stored exact-text vectors and encodes missing text; `--force` re-encodes every chunk. History remains stored within the new database; vector reuse itself needs no automatic republication. See the fresh-schema requirement below.

Entrypoints are in `src/cli/`, package processing in `src/ingestion/`, metadata validation in `src/catalog/`, database code in `src/database/`, tests in `src/tests/` and manual utilities in `scripts/`. The old intent/scoring kits are retired; their history remains in Git and private datasets/runs/ZIPs stay untouched under `.tmp`. `main.py` remains a thin database-check dispatcher. See the [application command reference](src/cli/README.md) and [manual utility reference](scripts/README.md) for command selection, status, prerequisites and effects. Source rename manifests, journals and `rename-history/` remain historical evidence.

The current schema head is `d8e031c72b19`: three conversation tables after the `c7d92e6a104b` guards and provisioned 14-table `bce4184a7f51` baseline (17 tables total). Upgrades are explicit and additive. Fresh databases require `vector` before the baseline. Older `0001`–`0006` histories are not automatically converted. See [database design](docs/database-design.md), [conversation provisioning](docs/conversations.md#enablement-and-storage) and [corpus publication](docs/corpus-publication.md).

See [ARCHITECTURE.md](ARCHITECTURE.md) and [AGENTS.md](AGENTS.md).

[Cross-domain tasks and evaluation](docs/business-topics.md) cover the corpus with
declared task/source bindings, bounded evidence supplementation and structured
follow-ups. The question bank has separate per-turn oracles and an explicit
fixture/readonly evaluator. No learned artifacts or env switches are added;
packages are not published and services are not restarted.

Offline corpus preparation on 2026-11-13 covers 28 PDFs / 334 physical pages, including twelve software manuals and the labor rules. See [package coverage and verification](docs/chunk-coverage-verification.md) for authoring changes, local-model evidence, held metadata and publication limits. Prepared packages are not automatically available through the API; database publication and corpus-matched relevance calibration remain separate operations.

Subsequent PostgreSQL publication on 2026-11-13 verified 13 eligible packages / 324 active vectors: 12 identical-run skips and one new benefits run (29 vectors). At that publication snapshot, fifteen packages / 296 chunks remained held; this is historical, not the current on-disk eligibility inventory. Real-database regression retrieval found required top-5 evidence for 51/53 supported evaluated cases; 77 cases requiring held sources were deferred. See [publication checks and limits](docs/chunk-coverage-verification.md#postgresql-publication--2026-11-13). No relevance profile or API rollout was performed.

Lexicon and template-catalog edits belong in the small declared JSON files under
each artifact group's `source/` directory. Run `python -m src.cli.build_artifacts build`
to regenerate the tracked runtime files, then `python -m src.cli.build_artifacts check`
before catalog/inventory release checks. Runtime still reads the same monolithic
files. See [source layout, import and recovery](docs/runtime-artifacts.md#split-source-authoring).

## Current verification status

### External stateless HTTP E2E harness construction - 2026-11-18

The new [external HTTP harness](docs/http-e2e.md) constructs question-bank and
protocol scenarios for both core endpoints of a manually started bot. Inventory
is local-only; prepare/run are explicit future operations with readonly sources,
reviewed private bindings, health gating and endpoint-specific assertions.
No application serving behavior, question oracles, artifact bytes or private
configuration were changed by this task. Existing working-tree changes were
preserved. Live execution belongs to a separate plan.

Post-edit local construction checks at `109b042` plus this working tree passed
**49 tests** in the new harness and adjacent answer-evaluation suites, with the
existing Starlette/AnyIO deprecation warning. These tests use fictional HTTP and
SQL connections. The inventory CLI compiled **1,696 checks** from 832 bank cases:
**1,650 pending** and **46 not applicable**, not E2E passes. Its private output is
`.tmp/http-e2e-construction-inventory-20261118.json`; input hashes identify that
compilation snapshot rather than a deployment. New documentation paths/anchors,
example JSON, syntax and scoped whitespace were checked.

Validation used the repository Python environment and fresh extended-length
`.tmp` test directories outside the sandbox's known fixture/read restrictions.
No full application regression, live HTTP, HRM/corpus DB access, model startup,
seed/reset, migration, service restart or real E2E execution was performed.
Neither `prepare` nor `run` was invoked against live dependencies; these await
the separate execution plan. No staging, commit or push was performed by this task.

### Structured conversation plans - 2026-11-18

On `179b330` plus this working tree, conversation continuations update a bounded
structured request instead of concatenating replies into new query prose. The
same plan reaches personnel reads, evidence selection and rendering. Pending
requests survive unclear replies; choices keep the original subject/dates/assertions;
aspect follow-ups replace the active aspect unless an addition is explicit.
General leave guidance precedes software selection; explicit software requests
use registry-owned platform terms. Mixed document/profile requests retain both
parts, ambiguous names never establish self identity, and record-specific HRM
errors no longer disable another employee's reads. See
[request planning architecture](ARCHITECTURE.md#request-planning-and-completeness).

HTTP and stored answers remain schema 2; internal context is schema 5. Deploy
code and generated catalogs together after draining the old worker. Old derived
context may require the complete question again, without deleting history or
regenerating saved answers. No migration, schema-2 reset or Backend/portal change
is required. Existing working-tree changes and the user's `mock/` data were preserved.

Final non-integration regression passed **1,088 tests**, with **1 skipped and
57 integration tests deselected**, retaining the existing Starlette/AnyIO warning.
The new HTTP conversation suite uses fictional evidence and durable memory,
covering punctuation/platform replies, pending preservation, cache rebuild/replay,
task choices, subject/date retention, aspect replacement, mixed lookup, record
failure isolation and limited work-date facts without policy-tenure certification.
The frozen source-fixture runner passed **849/849 turns in 832 cases**; a separate
paraphrase/continuation check passed **14/14**. Reports are private under
`.tmp/structured-turn-questions-06.json` and `.tmp/structured-turn-paraphrases-02.json`.
These exposed regressions are not independent business acceptance.

Artifact build/check, catalog validation (279 tasks/29 documents/530 chunks),
source impact (1,346 unchanged references) and runtime inventory (22 files)
passed. Current manifests bind the frozen reports/catalogs and retain the prior
regression snapshots; model bytes and evidence bindings remain unchanged.

Exactly two general-guidance case expectations were reconciled with the user's
selected behavior, strengthening source/claim assertions and retaining exclusions;
see [question sources](artifacts/questions/README.md). Other oracles remain intact.
Initial runs caught compatibility, clause/subject and fixture-path regressions;
only the final runs above are passing evidence. Successful checks used the
repository Python environment and separate extended-length `.tmp/` directories,
without permission changes or cleanup of existing temporary trees.

No live HRM/model/database validation, disposable-DB integration, publication,
migration, conversation reset, service restart or deployment was performed.
The running deployment and live transaction behavior remain unverified. Earlier
entries below describe their own snapshots.

### Request planning, identity and recovery - 2026-11-18

At implementation commit `179b330` (based on `91669f7`), plus the final
documentation/manifest updates in this working tree, shared request analysis
preserves recognized policy/profile combinations, rejects third-party profile
substitution and asks about unresolved companion requests before personnel reads.
Explicit document lookup stays separate from profile access and task ambiguity.
Childbirth-policy clarification and numbered choices retain requested aspects.
Schema-2 HTTP and stored results are unchanged; reducer schema 4 invalidates only
derived context. See [request planning architecture](ARCHITECTURE.md#request-planning-and-completeness)
and [compatible rollout](docs/contextual-answers.md#conversations-and-rollout).

HRM checks can recover after startup/read failures with serialized, bounded
rechecks; disabled HRM never retries. Answer/retry deadlines include conversation
storage, with transaction-local lock/statement limits and existing cancellation,
idempotency and purge protection. Deploy code and generated catalogs as one set
after draining the old worker; no migration or schema-2 conversation reset is
required. No running service was restarted by this change.

Final non-integration regression passed **1,059 tests**, with **1 skipped and
57 integration tests deselected**; the existing Starlette/AnyIO deprecation
warning remains. The preceding focused run passed **141 tests**; the final full
run includes subsequent coverage of authored conjunctions, labelled inputs and
manager-subject/projection boundaries.
The unchanged source-fixture question oracles passed **849/849 turns in 832
cases**, and a separate targeted paraphrase/numbered-choice check passed **12/12**.
These are exposed implementation regressions, not independent business acceptance.
Reports remain private under `.tmp/answer-plan-questions-04.json` and
`.tmp/answer-plan-paraphrases-02.json`. Artifact build/check, catalog validation
(279 tasks/29 documents/530 chunks), impact (1,346 unchanged references) and the
runtime inventory (22 files) passed. Historical manifest results were retained;
current hashes bind the regenerated catalogs and manifests without changing models.
New documentation targets/anchors and CRLF-aware whitespace checks passed;
generated runtime CRLF bytes remain builder-owned.

Earlier attempts encountered sandbox access errors and caught over-conservative
clause splitting; those attempts are not passing evidence. The final evaluator
keeps its original oracles. The processing-failure privacy test retains its
assertions with the canary inside the recognized clause, so it exercises the
failure path rather than the new clarification guard. Successful tests used the
repository Python environment and fresh extended-length `.tmp` paths without
changing permissions or deleting existing temporary trees.

No live HRM/model/database validation, disposable-DB integration, publication,
migration, conversation reset, service restart or deployment was performed.
Transaction settings were verified at the mocked boundary; live lock/commit
behavior remains an integration check. Older entries describe their own snapshots.

### Unified contextual answers - 2026-11-18

This working tree replaces the A/B/mixed HTTP variants with schema 2 unified
evidence and requires trusted HRM identity on all answer/retrieve payloads.
Catalog schema 3 declares all 279 task dispositions. Backend and portal must
deploy together with the bot; old development chat requires the explicit reset
runbook. See [contextual answers](docs/contextual-answers.md).

On the working tree following `0764a30` (including concurrent changes now at
`0ce7c9e`), full non-integration regression passed **1,037 tests**, with **1 skipped
and 57 integration tests deselected**. Subsequent conflict/contract refinements
passed **115 focused tests**; the final reset error-sanitization refinement passed
**10 reset tests**. The final source-fixture evaluator passed **849/849 turns in
832 cases**, with hashes for the contextual/snapshot modules. Static oracles were
reconciled for newly declared task fields and soft HRM failures; policy source,
forbidden-source and amount assertions remain. This is not independent business
acceptance. Artifact build/check, catalog validation (279 cards/29 documents/530
chunks), source impact (1,346 unchanged bindings) and runtime inventory (22 files)
passed. The existing Starlette/AnyIO deprecation warning remains.

No live HRM/model/database validation, publication, migration, actual development
chat reset or deployment was performed. Reset help and fictional guards were
verified; dry-run/apply against actual databases require explicit development
targets and stopped services. The pre-existing employee-store consolidation and
concurrent pgvector documentation changes remain. Older counts below are historical.


### pgvector provisioning documentation - 2026-11-18

Documentation-only reconciliation on working tree based on `0764a30`, preserving
pre-existing and concurrent local changes. The [database guide](src/database/README.md#fresh-database-provisioning)
now separates server installation, per-database extension provisioning and Alembic
execution; current descriptions agree on head `d8e031c72b19` and 17 application
tables (14 corpus/publication plus 3 conversation tables).

Post-edit static review matched the revision chain, table counts, extension
ordering, configuration precedence and sanitized diagnostic mapping against
source. Migration-file SHA-256 hashes matched the pre-edit snapshot. Scoped
`git diff --check` passed. Local Markdown link/anchor inspection found three
unchanged references to the missing `docs/retrieval-evaluation.md#known-evaluator-reporting-gap`
anchor (two in this README and one in ARCHITECTURE); links introduced by this
update resolve. No migration/runtime/test/configuration files were edited by this
task. No migration, database connection, integration test, dependency installation
or application test suite was run; this review does not establish live PostgreSQL
state, permissions or migration execution success. Earlier verification entries
below describe their own snapshots.

### Type B employee-store consolidation - 2026-11-18

At `0764a30` plus this working tree, employee-source implementation moves into
`src/type_b/employee_store/`, with a shared field registry, immutable contracts,
employee-ID reads and one-hop manager summaries. The removed Python package has
no compatibility re-exports; the preflight CLI command remains unchanged.
HTTP request/response contracts retain optional hrm_user_id and nine exposed
facts. No Type A personalization, source writes, migration, environment change,
artifact regeneration or Backend/portal change accompanies this work. See
[service contract and manager resolution](docs/employee-source.md#internal-service-interface).

Final post-edit non-integration regression passed **1,046 tests**, with **1 skipped
and 57 integration tests deselected**. Focused employee-store/Type B/retrieval HTTP
checks passed **210 tests**, with **3 HRM integration tests skipped** because the
disposable fixture was not configured. These results cover fictional inputs,
query shapes, relation outcomes, cancellation/draining and import isolation;
they do not establish live HRM schema, privileges, relation correctness or query
performance. The existing Starlette/AnyIO deprecation warning remains.

The pre-edit focused attempt encountered Windows pytest temporary-directory
permissions and did not establish a clean baseline. Successful post-edit runs
used the repository Python environment and fresh extended-length `.tmp` paths
outside that restriction. The initial full regression caught an eager reader
type import pulling SQLAlchemy into API import; this was corrected with a
type-checking-only import, retaining the assertion, before the successful full
rerun. Syntax, affected documentation links, removed imports and whitespace were
also checked. No live preflight, deployment or service restart was performed.

### Cross-domain tasks and evidence - 2026-11-18

Broad post-edit validation at `68b2456` plus the staged/unstaged cross-domain follow-ups
passed **1,010 non-integration tests**, with **1 skipped / 56 deselected** and
the existing Starlette/AnyIO deprecation warning. The run used
`--basetemp '\\?\D:\sources\rita-id\rita-vo-bot\.tmp\business-full-20261118-04'`
and `-o cache_dir=.tmp/business-full-cache-04`. The subsequent dependency-report
follow-up also passed **191 focused tests**, including related ambiguity/negative
case discovery after a source change. Disposable PostgreSQL integration suites
require a separate test database and were not executed.

The selected set contains **279 tasks / 320 concepts**, **446 retrieval bindings /
285 routes**, and dispositions for **530 chunks / 29 packages**: 445 maintained
template evidence chunks and 85 explicit-lookup/context chunks. Source impact
verified **1,346 unchanged references**, with no affected tasks/cases. Build/check,
catalog validation, all **22 runtime assets**, question/task/aspect/route coverage
and stable original 416 cases / 433 turns passed structural checks.

The expanded **832-case / 849-turn** source-fixture HTTP/reducer evaluation passed
**849/849**. Real Type A readonly evaluation passed **785 turns**, with **zero
failures / 64 explicit fixture/personnel deferrals**. Its stratified **40-task**
retrieval sample measured recall@30 **100%**, expected-first ordering **24/40
before to 40/40 after** metadata ordering, and ordering p95 **10.81 ms**. The
baseline is the same supplemented pool before metadata sorting, not a historical
or vector-only baseline. Reports are private under `.tmp/business-*-final-v3-20261118.json`.
These are exposed regressions, not independent business/quality acceptance or an SLA.

Source audit corrected referral-rate, office-equipment and forklift references;
conflicting inpatient sources now require an explicit choice. Context schema 2
adds bounded user slots; mixed HRM failures retain independent policy evidence
with the additive employee `unavailable` status. See [contracts and maintenance](docs/business-topics.md).
No packages/source snapshots, model bytes, private `.env`, migrations, publication,
personnel data or running services were changed. Earlier verification entries
below describe their own historical snapshots.

### Shared HTTP service authentication - 2026-11-18

All HTTP business endpoints and readiness probes now use CHAT_BOT_SERVICE_KEY.
Conversation/answer/Type B switches remain separate; TYPE_B_SERVICE_KEY is retired.
Python still binds loopback; remote Backend access uses HTTPS through a separate
proxy. See [service authentication](docs/http-service-auth.md). Private `.env`,
corpus/model bytes, migrations and running services were not changed by this task.
Post-edit non-integration regression passed **986 tests**, with **1 skipped** and
**56 integration tests deselected**. The final mount-prefix auth hardening then
passed **75 focused auth/answer tests**, including disconnect/timeout draining.
Starlette/AnyIO reports its existing deprecation warning. All HTTP fixtures use
fictional credentials and isolate the developer's private `.env`; no live DB or
model-quality acceptance is claimed. An earlier full run had one configuration
fixture missing the newly required key, corrected without removing its assertions.
The initial HTTP middleware implementation failed the disconnect regression and
was replaced with pure ASGI middleware; that regression remains enabled.

Successful runs used the Python 3.14 repository virtual environment and fresh
`.tmp` test/cache directories outside the existing sandbox permission restriction.
No proxy, public-domain smoke test or service restart was run. Current verification
reflects executed working-tree snapshots; concurrent catalog/source work was kept.

### Questions and Retrieval Git policy - 2026-11-18

The Git allowlist now exposes the question source dataset and the Retrieval
catalog/manifest and declared source collections. The inventory checker permits
only indexed question paths outside runtime assets; it does not execute or grade
the cases. See [Git policy and limits](docs/runtime-artifacts.md#git-policy-for-questions-and-retrieval)
and [question sources](artifacts/questions/README.md). Existing unrelated working-tree
changes are preserved; no artifact regeneration, staging, commit or push is part
of this change.

Post-edit validation at `540badc` plus the existing working tree passed **93
tests** in the runtime-inventory/Git-policy and artifact-authoring suites.
`python -m src.cli.build_artifacts check` returned current with zero changed
files; runtime inventory verification passed for **22 files**. Data checks also
passed for **416 question cases / 433 turns**, including JSON/IDs, counts, paths,
duplicate scenarios and coverage links. Scoped whitespace and new documentation
links were checked.

The initial sandbox run could not access pytest fixture directories and some
catalog/index files. The successful checks ran outside that restriction, using
the repository virtual environment and a fresh extended-length `.tmp` basetemp;
no permissions or artifact bytes were changed to obtain a pass. No full application
suite, integration DB, model inference, bot scenario execution or deployment was
performed. Git staging/commits in tests occurred only in fictional temporary
repositories, not in this working checkout.

### Leave recognition and source-bound ordering - 2026-11-18

The optional retrieval catalog adds bounded leave-query recognition and source-bound stable ordering after the reranker and before top-K. The original two-day question now promotes the general leave procedure; `/answer` asks for leave type/platform instead of returning `task_unavailable`. Six partial source-bound tasks and fifteen recognition-only concepts bring the inventory to 58 tasks / 267 concepts, with 32 ranking bindings. Recognition-only phrases do not rewrite retrieval queries. No chunks, vectors, DB schema, employee access, public response shape or runtime env switches were changed by this task. See [behavior, source updates and limits](docs/leave-retrieval.md).

Final post-edit non-integration regression: **912 passed, 1 skipped, 56 deselected** at `540badc` plus the leave working tree, with the existing Starlette/AnyIO deprecation warning. The focused final leave suite passed **55 tests**, including serialized HTTP/CLI and specified-task evidence. The full run includes concurrent conversation work, which was preserved. Windows checks used an extended-length `.tmp` root. Integration tests were not executed against a disposable DB.

Readonly real-corpus evaluation improved expected-first ordering from **4/7 to 7/7** exposed cases; warm ordering p95 was **1.31 ms** (100 measurements after 10 warmups). The reported question changed from E-Leave's session example first to the general leave procedure first. A readonly answer probe returned targeted clarification for the original question and source-backed partial answers for five specified branches at top-K 1. Private metadata-only reports are under `.tmp/leave-retrieval-20261118/`; these results are not independent human quality acceptance. No migration, publication, service restart or deployment was performed. Final build/check, catalog validation, all **143** source bindings, runtime inventory (**22 files**) and Markdown paths passed. CRLF-aware diff inspection found no whitespace errors.


### Durable conversations - 2026-11-18

Backend/bot implementation adds owner-scoped room APIs, durable turns/messages,
structured context/cache recovery and purge/tombstones. New features default
off; portal, private `.env`, live services and published corpus were not changed.
Post-edit bot non-integration regression passed **857 tests**, with **1 skipped**
and **56 integration tests deselected** (before the final self-reference guard
and immutable catalog-identity optimization). The subsequent focused
conversation/HTTP/lifecycle/Type B regression passed **143 tests** after those
changes. Starlette/AnyIO reports its existing deprecation warning. The optional
conversation PostgreSQL fixture was **skipped** because
`TEST_CONVERSATION_DATABASE_URL` was not configured; no live transaction,
locking, migration execution or end-to-end answer quality is claimed.

Validation used the Python 3.14 repository virtual environment. The full and
focused runs used fresh named `.tmp` basetemps/cache directories outside the
sandbox's existing pytest permission restriction. Earlier attempts caught an
import-time SQLAlchemy regression and the old 14-table schema assertion; both
were corrected while retaining import isolation and full 17-table DDL checks.
Existing unrelated source/catalog/artifact work continued concurrently and was
preserved. This records the executed snapshots, not acceptance of those changes.
See [operation, migration and limits](docs/conversations.md).

Final schema/context checks passed **37 tests**, including both new turn indexes
and the immutable migration-to-model comparison. Scoped diff whitespace passed;
an unrestricted diff check hit a permission error on a concurrently edited
catalog artifact, which was left untouched.

### Shared memory cache helper - 2026-11-18

The application now owns an async JSON memory cache; see
[cache usage and limits](#shared-memory-cache). This change does not activate
answer, retrieval, HRM or conversation caching. Validation used Python 3.14.10
and the repository virtual environment with pinned aiocache 0.12.3, against the
working tree at `2344fa0` plus the cache debug-log hardening and documentation.
Existing unrelated working-tree edits were preserved.

Post-edit focused helper/lifecycle/HTTP checks passed **90 tests** before the
additional debug-log regression. The subsequent full non-integration run,
including that regression, passed **847 tests**, with **1 skipped** and
**55 integration tests deselected**. Both successful runs reported the existing
Starlette/AnyIO deprecation warning. The initial sandboxed focused run had
**82 passed / 8 setup errors** because of pytest temporary-directory permissions;
it is not a passing run. Successful reruns used separate fresh `.tmp` basetemps
and cache directories outside that sandbox restriction, without changing old
directories or permissions. Full command:

```powershell
.\env\Scripts\python.exe -B -m pytest -m 'not integration' -q --tb=short --basetemp '\\?\D:\sources\rita-id\rita-vo-bot\.tmp\cache-helper-full-20261118-01' -o cache_dir=.tmp/cache-helper-full-cache-20261118-01
```

Use a fresh basetemp when repeating. No integration database, model startup,
live backend, service restart, migration or conversation feature was exercised.
Diff whitespace and the new documentation paths/anchors were checked.


### Catalog tasks and linked evidence - 2026-11-18

Implementation started at `b84680e`; the current checkpoint is `2344fa0` with local follow-ups. The runtime now uses schema-v2 task/evidence catalogs with direct eligible DB reference reads; intent/profile training, calibration and Colab kits are removed. Retrieval HTTP/CLI return unassessed candidates. Twenty source-checked procedure tasks and concepts extend the inventory to 52 tasks / 252 concepts, with dispositions for 29 packages / 530 chunks. Source coverage is not answer completeness or human quality acceptance. See [current operation and limitations](docs/catalog-answering.md).

Post-edit validation at `2344fa0` plus local follow-ups: **847 passed, 1 skipped, 55 deselected** in the non-integration suite using an extended-length `.tmp` root on Windows; one existing Starlette/AnyIO deprecation warning. The run includes concurrent cache-helper work, which is outside this task and remains preserved. The final affected suites also passed (187 tests before the last structure-only cleanup). Artifact build/check, catalog validation, 81 unchanged source bindings, runtime inventory (20 files), local pinned ONNX smoke (one fictional passage), local Markdown paths and CRLF-aware diff checks passed. Integration tests collected, but were not executed because no disposable test DB URL is configured. No application DB publication, service restart, target DB compatibility or live answer-quality acceptance is claimed. Earlier ordinary-path runs hit Windows MAX_PATH/setup limits; the final extended-path run resolved them.

All subsequent dated verification entries are historical snapshots; removed commands, retired gates and earlier runtime contracts below do not describe the current implementation.

### Intent Colab workflow notebook - 2026-11-17

The [retired intent notebook](docs/intent-classifier.md) now
orchestrates the existing CLI: verified kit extraction, dependency installation,
local model/device selection, private dataset setup/review, preflight, training
and hash-bound export. It is stored with empty outputs and included in the
packager allowlist/source identity. This work continued across the concurrent
`369cfa4` commit; existing user edits were retained.

Post-edit notebook-focused checks passed **12 tests** using synthetic fixtures,
mocked upload/install/device/train/download boundaries and an actual extracted-CLI
validation/review rejection without a model or database. Existing runtime intent,
evaluation and preparation regressions separately passed **60 tests**, with the
existing Starlette/AnyIO deprecation warning. The sandboxed notebook-only pytest
run also reported a cache-write permission warning; its assertions passed.

The full local kit verifier remains **31 passed, 6 failed, 2 errors, 1 skipped**:
the preserved tracked sample has `reviewed=true` while its generator and agent
review notes still describe an unreviewed draft, so source/hash/review and
packaging checks reject it. The skip is actual Windows symlink creation.
These assertions remain enabled. Per the user's explicit choice, this task does
not reset that sample or produce a new release ZIP. An old ZIP lacking the
notebook cannot be used with the notebook setup cell; resolve source consistency
and reverify before creating a matching release archive.

Static review passed for **127 local documentation links**, all **11 notebook
code cells**, empty execution outputs, Git LF attributes and diff whitespace.

No live Colab/CUDA run, dependency installation, real training, database benchmark,
acceptance/promotion, `.env` change or service deployment was performed.

### Relevance scoring V3 Colab development kit - 2026-11-17

The independent [V3 kit](docs/relevance-scoring.md) packages numeric
development fitting without a source clone, model or database. Optional GPU
scoring is diagnostic; final acceptance/promotion stays on the local CPU pipeline.
The exchange utility exports reviewed numeric rows and independently re-fits
imported candidates; it never creates a runtime profile or changes `.env`.

Implemented in the uncommitted working tree based on `1961fc9`, preserving the
concurrent standalone intent-kit work. Post-edit focused non-integration checks
across the V3 kit, canonical scoring/training/review, semantic evaluation and
promotion passed **136 tests**, including **28 kit cases**. The extracted-kit
subprocess fitted fictional features outside the repository and its result was
independently re-fitted/imported locally. The notebook's code cells compiled with
empty outputs. The initial sandboxed run hit Windows temporary-directory access
errors; successful runs used new short `.tmp/rv3b`, `.tmp/rv3c` and `.tmp/rv3d`
roots outside the sandbox. No permissions or old temporary trees were changed.

The packaging command produced the private upload ZIP
`.tmp/relevance-scoring-v3-kit-1.0.0.zip`. Local links/anchors, command help and
diff whitespace were checked. Python 3.14.10 / NumPy 2.5.4 was used locally;
the pinned NumPy requires Python >=3.12. GPU checks used simulated absence/error
boundaries, not actual CUDA. No real Colab session, clean dependency install,
full application regression, model inference, database, real-data collection,
training, acceptance, promotion, service restart or deployment ran. Fictional
fitting proves exchange compatibility, not real retrieval quality or a usable
relevance profile. Canonical runtime source, `.env`, models and selected runtime
artifacts are unchanged by this kit work.

### Git-managed standalone intent Colab kit - 2026-11-17

Implemented in the uncommitted working tree based on `1961fc9`. Reusable kit
source and its 600-case fictional unreviewed sample now live under
`collabs/intent-colab/`; existing ignored local kit/workspaces remain intact.
See the [kit commands and boundaries](docs/intent-classifier.md). No runtime
API, model bytes, selected artifacts or `.env` were changed by this migration.

Post-edit focused non-integration regression across the Colab kit, runtime intent,
intent evaluation and preparation suites passed **87 tests, 1 skipped**, with the
existing Starlette/AnyIO deprecation warning. The skip is actual symlink creation
on this Windows account; the mocked link guard passed. This includes synthetic
numerical sklearn training parity, source/build consistency, dataset review gate,
standalone isolation, packaging allowlists/checksums and stale-receipt rejection.
The initial sandbox run had **48 passed, 1 skipped, 39 fixture setup errors** from
Windows temporary-directory permissions; the successful rerun used a fresh
`.tmp/ic-regression-02` temporary root outside the sandbox, without changing
permissions or deleting previous workspaces. Build/sample `--check` modes passed.

The standalone verifier also passed **27 checks, 1 skipped** and supplies a
source-bound receipt for a newly generated private upload ZIP. Git attributes
select LF for kit source and samples; source snapshots normalize UTF-8/LF while
model/archive checks remain raw-byte hashes. The sample is still `reviewed=false`.
Static review checked **170 local documentation links**, parsed seven kit/test
Python files, confirmed unchanged sample content, and passed diff whitespace,
Git attribute/ignore and upload checksum checks.
No real E5 training, CUDA/Colab execution, database benchmark, acceptance/promotion,
service restart, full regression suite, Git commit or push was performed.

### HTTP Type B work-profile integration - 2026-11-17

Working tree based on `996ba0f`. Both HTTP endpoints now accept optional
hrm_user_id and provide opt-in trusted-backend work-profile evidence/templates.
Type A wire shapes, readonly HRM access, document eligibility and relevance
semantics are preserved. See [Type B contract](docs/type-b-http.md) for the service
key, supported fields, separate mixed evidence, lifecycle and deployment limits.

Post-edit full non-integration regression: **1036 passed, 1 skipped, 54 deselected**
using a fresh short temporary root under `.tmp`. Focused Type B plus optional
source integration: **84 passed, 2 skipped**; PostgreSQL checks were not configured.
The initial default-temp full run had **1007 passed, 1 skipped, 15 fixture setup
errors** from Windows path length in the existing asset-restructure suite; a
short-temp rerun passed, and the final full run includes the later matcher guards.
All runs retained the existing Starlette/AnyIO deprecation warning. Syntax,
new documentation links, nine-field OpenAPI contract and final diff whitespace
checks passed. Tests use fictional source/model boundaries, not real HRM data or
evidence of classifier/answer quality or live backend authorization.

No .env edits, real HRM/application database access, migration, real-model inference,
artifact rebuild/promotion, service restart/deployment, backend or portal changes
were performed. Type B stays disabled by default until separately provisioned
readonly access and the authorized caller's ID/header contract are verified.

### Split artifact authoring sources - 2026-11-17

Implemented from the clean `7126f2b` checkout; results describe this uncommitted
working tree. The selected lexicon/catalog now have 295 declared authoring files:
232 concepts, 32 cards, coverage for 29 documents and two ordered source indexes.
Offline import/build/check and the exact source-path inventory exception are
implemented; runtime readers and artifact schema-v1 contracts are unchanged.
See [source authoring and recovery](docs/runtime-artifacts.md#split-source-authoring).

Initial focused authoring/inventory checks passed **48 tests**; the broader
affected semantic/answer/promotion run passed **183 tests** before the last
import-formatting, staging-membership and file-type guards. The full run before
the final regular-file guard,
`python -m pytest -m 'not integration' -q` passed **952 tests, 1 skipped,
54 deselected**. The final authoring/inventory rerun after the regular-file guard
passed **52 tests**. These overlapping runs are not additive. The existing
Starlette/AnyIO portal deprecation warning remains in the broader/full runs.

Initial sandboxed runs failed on Windows pytest temporary-directory access;
successful runs used fresh `.tmp/as02`, `.tmp/as04`, `.tmp/as05` and `.tmp/as06`
basetemps with separate caches outside the sandbox. No old temporary trees or
permissions were changed. One intermediate focused invocation selected a
nonexistent test filename and ran no tests; the corrected invocation is the
183-test result above.

`build_artifacts check` passed; a real `build` was a no-op (zero changed files).
Catalog validation passed for **29 documents / 530 chunks / 32 cards /
232 concepts**. Runtime inventory verification passed for **20 files**;
all **295** declared source files are visible through the Git allowlist.
Byte comparisons against `HEAD` confirmed that both generated runtime files,
both provenance manifests and `runtime-assets.json` are unchanged. Local
Markdown links/anchors and `git diff --check` were checked.

No dependency, model, package, environment, database, backend or portal changes;
no deployment/restart, integration database checks, real-model evaluation,
publication or calibration. Source/integrity validation does not establish
semantic acceptance. Future content edits require explicit manifest/inventory
and compatible pipeline reconciliation before release; build does not do that.

### Stable current artifact layout - 2026-11-17

Implemented against the initially clean `e01fdee` checkout; results below validate
this uncommitted working tree. Runtime JSON/manifest pairs now live directly
under `artifacts/answers/` and `artifacts/semantics/`, with the same policy for
future eligible rerank/intent artifacts. Git retains history. Selected catalog
and lexicon data are byte-identical to the former `answers/v1` and `semantics/v2`
files; acceptance states remain unchanged. Defaults, allowlists, inventory and
the gated profile promotion utility follow the stable layout. See the
[transition and configuration checklist](docs/runtime-artifacts.md)
and [replacement/recovery workflow](docs/runtime-artifacts.md).

The focused promotion/inventory/answer run passed **102 tests** before the final
default-path/fallback regressions. Final post-edit
`python -m pytest -m 'not integration' -q` passed **929 tests, 1 skipped,
54 deselected**, including those regressions and replacement interruption tests.
Successful runs used fresh `.tmp/artifact-layout-01` and `.tmp/artifact-layout-02`
basetemps with separate caches outside the sandbox after the initial sandboxed
attempt encountered Windows temporary-directory access errors. No existing
permissions or temporary trees were changed. The existing Starlette/AnyIO portal
deprecation warning remains.

`python -m src.cli.check_answer_catalog` passed: 29 documents, 530 chunks,
32 cards and 232 concepts. `python -B -m scripts.verify_runtime_assets` passed
for **20 files** using an isolated temporary Git index/object store representing
the complete artifact moves; the repository index was left untouched. Against
the actual unstaged index, that command reports six `tracked_path_not_allowed`
errors for deleted legacy paths until the moves are staged/committed. An initial
isolated-index attempt could not write Git objects inside the sandbox; the
successful attempt kept both index and new objects entirely under `.tmp/`.
Byte comparisons against `HEAD` confirmed the selected data files are unchanged.

Local Markdown links/anchors and `git diff --check` were checked after the docs
update. No private `.env`, model bytes, chunks, database, backend or portal were
changed. No live service restart/deployment, real-model or HTTP smoke,
integration database tests, migration, publication, calibration or actual profile
promotion ran. Private configuration still needs explicit path review before a
restart; passing identity/integrity checks is not semantic acceptance or backup
verification. Earlier verification entries use historical artifact paths.

### HTTP configuration precedence and answer diagnostics - 2026-11-17

Validated the implementation working tree against `7c5957c`; pre-existing
documentation/test changes were preserved (and committed separately during this
task). HTTP startup now gives repository `.env` precedence over process
environment, including database URLs; standalone CLI policies are unchanged.
Added `/health/answer`, fixed answer lifecycle reasons and allowlisted
configuration/processing diagnostics. Existing answer error contracts, retrieval
readiness and cancellation/busy behavior are retained. See [answer diagnostics](docs/answer-evaluation.md#configuration-and-validation)
and [rollout checks](#http-service-operation-and-rollout).

Post-edit focused HTTP/configuration/answer checks passed **104 tests**. An
intermediate non-integration run passed **916 tests, 1 skipped, 54 deselected**.
After adding the end-to-end fictional `.env` conflict/startup/answer regression
and request-ID correlation assertion, final
`python -m pytest -m 'not integration' -q` passed **917 tests, 1 skipped,
54 deselected**. The existing Starlette/AnyIO portal deprecation warning remains.
The first sandboxed focused attempt hit Windows temporary-directory access
errors; successful runs used verified-unused short `.tmp/ac2`, `.tmp/ac3` and
`.tmp/ac4` basetemps with separate caches outside the sandbox. No existing
temporary trees or permissions were changed.

Local Markdown link targets (**133**) and five referenced heading anchors were
checked, along with the documented API/catalog command module paths;
`git diff --check` passed. No dependencies, private `.env`, runtime assets,
catalog/packages, database data or other repositories were changed. No live
service restart/deployment, real-model/network smoke test, integration database
tests, migrations or publication ran. Synthetic ASGI checks verify configuration,
health and response wiring, not live dependency availability or semantic quality.
Restart the API explicitly after reviewing `.env`/launcher differences to apply
this behavior to an already-running process.

### Documentation and regression reconciliation - 2026-11-17

Validated the docs/test working tree based on `bbeec8c` (clean before this task).
Current operational guidance now uses baseline `bce4184a7f51` and additive guard
`c7d92e6a104b`, distinguishes their upgrade/downgrade boundaries, and separates
HTTP filtering overrides from CLI/evaluator defaults. Profile schema-v2 raw-max
and schema-v3 learned scoring remain distinct. The single-package evaluator's
semantic-off/intent-off limitation is explicit; its existing
[reporting gap](docs/retrieval-evaluation.md#known-evaluator-reporting-gap) remains.
Historical migration/Qwen verification records and their anchors are retained.

Seven new fictional regression cases cover ASGI answer disconnect/timeout,
worker/watcher cleanup and shared busy ownership, lifespan shutdown, missing or
invalid catalog isolation, retired answer-study execution, and CLI filtering
despite the HTTP environment override. Existing HTTP filter/rerank and source
identity regressions are retained. Focused answer/catalog, HTTP/retrieval/scoring,
employee-source and database/schema unit suites: **298 passed**. Full
`python -m pytest -m 'not integration' -q`: **900 passed, 1 skipped,
54 deselected**; the existing AnyIO portal deprecation warning remains.
These overlapping runs are separate results, not additive test counts.

The first sandboxed focused run encountered Windows access errors while creating
and cleaning pytest temporary fixtures. Successful focused/full runs used fresh,
verified-unused short `.tmp/dtr2` and `.tmp/dtr3` basetemps with separate caches
outside the sandbox; no old temporary trees or permissions were changed. Local
Markdown paths/anchors and concrete documented Python command module paths were
checked against the source; `git diff --check` passed. Catalog validation passed
for **29 documents / 530 chunks / 32 template entries / 498 excerpt-only chunks**;
runtime inventory verification passed for **22 files**.

Only documentation and tests changed. No runtime, dependencies, private `.env`,
model/artifact/package bytes, backend or database were changed. No integration
database tests, migrations, publication, real-model quality checks, network
listener/proxy checks or service restarts ran. Synthetic ASGI checks do not prove
real-network cancellation, semantic correctness, live HRM access or rollout readiness.

### HTTP-only service transport - 2026-11-17

Removed the health-only gRPC server, entrypoint, dedicated tests, direct
requirements and example environment settings. HTTP contracts, loopback binding,
one-worker execution and retrieval/answer behavior are unchanged. This validation
uses the uncommitted working tree based on `a324872`, preserving existing and
concurrent template-answer, employee-source and runtime-asset edits. See
[HTTP operation and rollout](#http-service-operation-and-rollout) for replacement
probes and deployment limits.

Before removal, focused HTTP/answer tests passed **83 tests** in the repository
virtual environment. The same **83 tests passed** after removal, and again in a
fresh isolated environment under `.tmp` installed from `requirements-dev.txt`.
That clean environment contains neither `grpcio` nor `grpcio-health-checking`;
FastAPI, application and API entrypoint imports passed without `grpc` or
`grpc_health`, and `pip check` reported no broken requirements. Optional real
embedding/ONNX dependencies were not installed in that clean environment.

Post-change non-integration regression: **893 passed, 1 skipped, 54 deselected**.
The initial sandbox run hit Windows temporary-directory permissions; tests were
rerun outside that restriction. The first broad run then had **878 passed,
1 skipped and 15 fixture setup errors** from Windows path lengths in historical
asset-restructure tests. A full rerun with a verified-unused short `--basetemp`
under `.tmp` passed without changing tests or deleting old temporary directories.
Existing deprecation warnings remain: AnyIO's portal alias in the repository
environment, and Starlette's HTTPX TestClient integration in the fresh environment.

Local Markdown link paths, replacement HTTP anchors and `git diff --check` passed.
No migrations, publication, real-model checks, live backend/HTTP smoke tests,
service restarts or external launcher/probe changes were performed. Synthetic
checks do not certify live connectivity, retrieval quality or production capacity.
The existing runtime environment, private `.env`, model and document bytes were
not changed; only the isolated verification environment was installed.

### Semantic + template replacement - 2026-11-17

The answer implementation working tree is based on `a324872`, preserving the separately developed
readonly employee source and its documentation. Concurrent unrelated employee-source
and gRPC edits are outside this answer validation scope. Qwen loaders, native runtime,
model verifier, acceptance gates and model-specific tests are retired; shared
source/citation, HTTP and cancellation regressions are covered by template tests.
The interrupted verifier-improvement proposal is not an implemented capability.
No model bytes, installed environments, `.env`, packages, vectors, database schema
or publication rows were changed. No listener was opened or service restarted.

Catalog/source validation passed: **29 documents / 530 chunks**, **32 template
entries**, **498 excerpt-only chunks**, bound to the unchanged semantic v2 lexicon
and package snapshot. Most initial templates preserve source wording and explicitly
report partial coverage; three benefit cards include authored natural-language
amount/conditions/paperwork sections. This is not complete conversational coverage,
business approval or relevance acceptance. Runtime inventory verification passed
for **22 files**; `pip check` passed.
All **149 local Markdown file targets** in the changed answer/setup documents
resolved, and final diff whitespace checks passed (Git's existing CRLF notices
remain). Anchor semantics and live browser behavior were not checked by that scan.

Post-edit focused checks passed **115 tests** before the additional multi-beneficiary
regression. Final full non-integration verification passed **923 passed, 1 skipped,
54 deselected**, with the existing Starlette/AnyIO deprecation warning. Initial
sandbox attempts hit Windows access errors in pytest temporary storage; successful
runs used new extended-length repository-local `.tmp/` paths outside the sandbox,
without changing permissions or deleting prior temporary trees. No integration
database tests or migrations ran.

Read-only real-service regression used a private exposed **66-case** set covering
two questions per document plus eight boundaries. Final `run-02` completed without
request errors: **57 partial, 8 needs_clarification, 1 answered**; median **2.405 s**,
empirical p95 **4.001 s**. Startup was measured separately (**16.488 s**). Median
process CPU time per request was **10.391 CPU-seconds**, about **35.4%** normalized
over twelve logical CPUs during each request; this is process-level measurement,
not total-machine utilization or a controlled comparison with prior Qwen runs.
Generation/verification timings are zero. Private receipts are under
`.tmp/template-answer-20261117/`.

The first exposed run revealed a multi-beneficiary question being reduced to one
topic by a global exclusion. Clause-aware ambiguity handling and a fictional
regression now preserve both requested topics and ask for clarification; the final
run supersedes that first result. Expected document sources appeared for 54/58
document-labeled questions; four questions with duplicate travel-document topics
asked for clarification. Source checks retained amount, eligibility, exclusions,
paperwork and variation caveats in the published benefit answer. These exposed
checks are not independent holdout, exhaustive semantic review, production p95
acceptance or personal-entitlement verification. Unfiltered results remain unassessed.

### Readonly employee source foundation - 2026-11-17

Added an independent, opt-in HRM connection/repository/service and 64-property
dictionary; no source data/schema writes, live HRM calls, migrations, backend
changes or Type B/answering integration were performed. See
[employee source](docs/employee-source.md) for readonly provisioning and semantic
limits. Work was validated in the uncommitted working tree at `3924222`, preserving
pre-existing answering/docs edits and concurrent changes outside this module.

Focused employee-source plus existing database tests: **79 passed, 2 skipped**
(the HRM integration tests require an externally preprovisioned disposable fixture).
The initial sandboxed run hit Windows temporary-directory permissions; the focused
suite passed outside that restriction using a separate cache under `.tmp`.
Full `python -m pytest -m 'not integration'` was attempted but stopped during
collection on unrelated answering contract imports: initially missing
`verdict_schema`; a diagnostic rerun excluding that pre-existing test then found
missing `Draft` imports in answering/evaluation/runtime/API suites while those
files were being edited concurrently. These tests were not weakened or changed
by this task; no full-suite pass is claimed. Unit doubles do not prove PostgreSQL
role enforcement, live schema compatibility or source meaning/completeness.

### Corpus publication and additive schema guards - 2026-11-17

The working tree started at `3ec7101` with user-owned answering edits and a
provisioned replacement baseline. During execution, commit `562ff44` recorded
the baseline/guard migration and initial test reconciliation. Remaining work
adds documentation and fresh-baseline integration coverage. Concurrent later
edits to `src/answering/pipeline.py` were preserved and are outside this task.

Before writing, read-only inspection found 14 empty application tables at
`bce4184a7f51` and vector already enabled. A private full backup and schema dump
were saved, and the full backup was successfully restored to a newly created
disposable database. Additive `c7d92e6a104b` restored four protection triggers;
application `alembic check` reported no schema/ORM differences. No table reset,
baseline rewrite or API configuration change was performed.

All **29 packages / 530 chunks** passed structural validation and local E5
preflight (maximum **421 tokens**). Publication completed in two dependency
waves: the original whole-corpus dry-run had three `relation_target_missing`
rejections because its selected targets were not stored yet. Each wave passed
dry-run before publication. Stored content, provenance, metadata, relations,
v3 projections and all **530 finite normalized 384-dimensional vectors** were
verified. Repeating ordinary publication skipped all 29 packages, kept every
non-attempt table row snapshot unchanged and left **58 successful attempts**.

Real, exposed unfiltered regression retrieval found exact expected top-5
evidence for **57/58** supported questions and a source hit for **58/58**. All
29 documents had at least one exact-evidence hit. Five outside-corpus probes
each returned five unassessed candidates; no rejection or answer-quality
acceptance is claimed. See [execution results and limits](docs/corpus-publication.md#execution-results---2026-11-17).

Validation: **63 focused non-integration passed**; **959 passed, 1 skipped,
52 deselected** in the full non-integration suite; **52 integration passed**
on the restored disposable database; a fresh database initially without vector
also passed its dedicated baseline test (**1 passed**). Broad regression ran
before the later concurrent answering-pipeline edits, so it does not certify
that subsequent working tree. Initial sandbox runs hit pytest temporary-path
permission errors; successful retries used new extended-length `.tmp/` paths
outside the sandbox, without permission changes or deleting old test storage.
The existing Starlette/AnyIO deprecation warning remains. Private receipts are
under `.tmp/corpus-publication-20261117/`; `.env`, package/snapshot, model and
initial user-edit hashes were verified unchanged. Services were not restarted;
no accepted relevance profile, answer report or API rollout was produced.
The two generated disposable databases were removed after verifying empty test
tables; the backup remains. `pip check` and 128 local Markdown links/anchors
passed. Scoped diff whitespace checks passed; a whole-tree check also reported
trailing whitespace in the separately edited answering pipeline, left intact.

### Explicit unfiltered answering - 2026-11-16

Working tree based on clean `900b00a`. The user-authorized combination
`ANSWER_ENABLED=true` / `RELEVANCE_FILTER_ENABLED=false` now permits native
answer generation without a relevance profile or answer report/review. Reranking
remains independent. The filtered branch retains its existing acceptance and
corpus checks; official offline evaluation still rejects unassessed candidates.
Normal answer responses add server-owned `relevance` metadata and retain
`unassessed`, `relevance_filter_disabled` and completeness warnings even after
successful verification. See [the unfiltered contract](docs/answer-evaluation.md).

Local `.env` was prepared only for the four approved answer/model/filter keys,
with the actual Q5 SHA-256; all unrelated parsed values were checked unchanged.
No credentials were copied to reports, and model bytes were unchanged. Existing
services were not restarted. New configuration/code requires an explicit restart
to affect a previously running API.

Post-edit focused regression: **175 passed**. Full non-integration suite:
**959 passed, 1 skipped, 51 deselected**, with the existing Starlette/AnyIO
warning. `pip check`, changed-document links/anchors and diff whitespace checks
passed. Tests cover the flag matrix, skipped report IO, technical startup checks,
metadata preservation/rejection, empty and rejected drafts, unchanged official
evaluation strictness and native grammar/length validation.

Real checks used the existing database read-only and the local Q5 through an
in-process ASGI transport, without opening a listener or restarting the user's
service. They exposed a pre-existing native grammar conversion failure: string
`maxLength=2000` expanded beyond the parser recursion limit. The grammar adapter
now omits only string maximum lengths; strict Pydantic limits remain unchanged.
Both actual Draft and Verdict grammars compiled after the fix.

The three previously discussed questions were then checked with independent
application lifecycles. **All three returned HTTP 504** at roughly 30.21-30.26
seconds. Retrieval took about 2.47-6.70 seconds; the first inference consumed the
remaining 23.35-27.60 seconds, before a verifier call. This proves the request
reached inference rather than being blocked by absent calibration/report, but
it does **not** establish usable answer latency or policy-answer quality.
Timeout still closes the runtime and disables answering until restart; the
30-second deadline and verifier were not weakened. Performance remains an
operational limitation requiring separate work.

Private receipts are under `.tmp/unfiltered-answer-20261116/`: the initial
native-grammar failure and subsequent sequential timeout run were preserved;
`three-question.private-03.json` contains the independent-case timings/responses.
These are exposed regression trials, not holdout, a 60-case study or user
acceptance. No profile, accepted report or review was created/modified. No
migration, publication, database integration suite, backend/portal change or
production deployment was performed.

Full-suite invocation (choose fresh paths on repeat):

```powershell
.\env\Scripts\python.exe -B -m pytest -m 'not integration' -q --tb=short --basetemp '\\?\D:\sources\rita-id\rita-vo-bot\.tmp\unfiltered-answer-20261116-full-01' -o cache_dir=.tmp/unfiltered-answer-20261116-full-cache-01
```

### In-process native Q5 runtime - 2026-11-16

Working tree based on `b0c3ec0`, preserving prior answer/scoring/documentation
changes and the existing source-data removal. Replaced the answer HTTP model
server with `llama-cpp-python==0.3.37` in the existing FastAPI process, using one
dedicated model worker and no additional answer port. The optional dependencies
are pinned in [requirements-answer.txt](requirements-answer.txt). Retired server
environment values are ignored without editing `.env`; API response contracts
and excerpt-only retrieval are preserved. Report schema v3 binds installed
native/binding/template/model identity; server-runtime reports cannot enable it.
See [native setup and cancellation limits](docs/answer-evaluation.md).

Installed the official PyPI source release after SHA-256 verification and built
a CPU wheel with the existing Visual Studio Build Tools 2026 for Python 3.14.10.
CUDA/Vulkan/BLAS were off and host-native CPU compilation was on. A first build
attempt hit Windows archive path-length limits; the successful build used short
repository-local `.tmp` paths without modifying third-party source. Only the
optional binding and diskcache were newly installed; no existing dependency was
upgraded. Build/wheel/native hashes and fictional smoke receipts are retained
under `.tmp/native-answer-build-20261116/`.

Actual Q5 smoke checks passed: load, template/tokenizer, constrained JSON,
independent second prompt, token-generation stop, native prefill abort,
coroutine cancellation/drain and idempotent close. A raw fictional JSON probe
measured about 0.45 seconds to load and 0.91 seconds to generate; a separate
application-wrapper probe completed start/warm-up in about 3.48 seconds and
cancelled/drained in about 0.014 seconds. These are individual fictional probes,
not policy-quality, production-load or p95 latency acceptance. Model bytes were
unchanged. Native inference cannot be force-killed safely in this process;
cleanup may exceed the 30-second cancellation deadline.

Post-edit focused tests: **152 passed**. Full non-integration regression:
**936 passed, 1 skipped, 51 deselected**, with the existing Starlette/AnyIO
warning. `pip check`, CLI help, changed-document links/anchors and diff whitespace
checks passed. Synthetic tests use a fictional native binding and cover shared
worker ownership, grammar/token parity, cancellation in prefill/generation,
late-result suppression, reset/release and acceptance binding. Evaluator
regression now checks the real answer configuration shape separately from
publication configuration, fixing their previous name collision.

Static preflight accepted the installed binding and Q5 metadata/hash, but remained
blocked by disabled relevance filtering, a missing accepted retrieval profile
and no supplied source-reviewed 60-case manifest. Its receipt is under
`.tmp/native-answer-preflight-20261116-a9764bef/`. Answering remains disabled;
no real-document generation, 60-case quality/user-acceptance run, production
latency/memory study, integration database test, migration, publication, service
restart, backend/portal change or `.env` edit was performed.

Full-suite command (use fresh paths on repeat):

```powershell
.\env\Scripts\python.exe -B -m pytest -m 'not integration' -q --tb=short --basetemp '\\?\D:\sources\rita-id\rita-vo-bot\.tmp\native-answer-20261116-full-01' -o cache_dir=.tmp/native-answer-20261116-full-cache-01
```

### Grounded Q5 answer implementation - 2026-11-16

Working tree based on `b0c3ec0`, preserving existing scoring, documentation,
source-data removal and untracked work. Added the separate disabled-by-default
`POST /api/v1/answer` and application-owned answer runtime/contracts/pipeline,
shared with offline evaluation. The candidate is now
`models/Qwen_Qwen3.5-0.8B-Q5_K_M.gguf`; Q4/4B results cannot authorize it.
See [answer behavior and acceptance](docs/answer-evaluation.md).

Post-edit full non-integration verification: **931 passed, 1 skipped,
51 deselected**, with the existing Starlette/AnyIO deprecation warning. `pip check`,
answer CLI help and local path/anchor checks (130 links across seven documents)
passed. Synthetic tests cover API serialization, source/citation handling,
acceptance identity binding, busy state, rejected drafts, timeout, repeated
cancellation, disconnect cleanup and optional prediction-layer GGUF metadata.
The first focused sandbox attempt failed with Windows temporary-directory
permissions; successful runs used fresh repository-local extended-length paths
outside the sandbox, without deleting old temporary trees or changing permissions.

Read-only inspection of the actual Q5 file confirmed **646,093,920 bytes**,
qwen35/0.8B, embedding size 1024, Q5_K_M type 17 and 24 base layers plus one
next-token prediction layer. Metadata and SHA-256 checks do not authenticate its
upstream conversion or establish runtime support. Static preflight remained
**blocked** by disabled relevance filtering, a missing accepted retrieval profile,
no supplied source-reviewed 60-case manifest and no supplied runtime executable.
The receipt is under `.tmp/answer-q5-preflight-20261116-e9c5eb30/`.

No real generation, quality/user-acceptance study, latency/memory benchmark,
database integration test, migration, publication, backend/portal change, service
restart or `.env` edit was performed. Model bytes were not changed or promoted.
Answering remains disabled until the exact Q5 study meets every gate, including
95% successful completion within 30 seconds and explicit user acceptance.

Full-suite invocation (choose new temporary paths when repeating):

```powershell
.\env\Scripts\python.exe -B -m pytest -m 'not integration' -q --tb=short --basetemp '\\?\D:\sources\rita-id\rita-vo-bot\.tmp\answer-q5-20261116-full-01' -o cache_dir=.tmp/answer-q5-20261116-full-cache-01
```

### Learned relevance scoring implementation - 2026-11-16

Implemented the user-approved local eight-feature logistic scoring experiment,
schema-v3 coefficient profiles and schema-v2 compatibility, development-only
five-fold fitting, score-free review/export, and gated byte-preserving promotion.
Models and 30 candidates remain pinned; public API fields and environment
configuration are unchanged. The target remains semantic lexicon **v1**, with
semantic expansion on and intent off. Provisioned lexical v2 remains intact.
See [scoring workflow and limitations](docs/relevance-scoring.md).

A new isolated schema-0006 database was provisioned and read back against the
current **29 packages / 530 chunks**. All 29 publications succeeded; five initial
package-lock rejections were retried only after confirming no run or attempt
existed for those revisions. Original database count/ID-hash snapshots were
preserved. The development proposal contains 300 questions and no accepted
candidate labels; source/alias approval is not retrieval-data approval. All
previously exposed source questions/families stay development/regression data.
Private receipts are under `.tmp/relevance-profile-20261116-06/`.

Verification: **885 passed, 1 skipped, 51 deselected** in the final non-integration
suite, including the review-bridge integration refinements. Native ONNX
smoke passed for two fictional passages, checking same-batch raw-max parity,
eight features and coefficient-bound fingerprint changes. Runtime inventory
passed for **20 assets**. Successful pytest runs used fresh extended-length
repository-local temporary directories outside the Windows sandbox after
sandbox setup failed with access-denied errors. The existing Starlette/AnyIO
deprecation warning remains. These checks establish implementation behavior,
not real-data scoring quality. No fitted real-data scorer, accepted profile,
promotion, API deployment or independent backup is claimed at this stage.

Real development collection completed for **300 cases / 18,000 arm-candidate
pairs**, with **9,062 unique case/passage pairs**. Before/after database/package
checks passed; runtime files, `.env` and both lexicons matched their recorded
hashes. The source-only preliminary candidate sets matched every full-collection
arm/case exactly. A score/rank-free source worksheet was generated and an actual
unapproved export was rejected without producing training data. The user
accepted the realism and family partition of an initial **96-case** prospective
acceptance-task packet only. A subsequent explicit review accepted all **200**
proposed acceptance-case definitions, supported/unsupported evidence hypotheses,
family partition and eight designated critical/top-1 cases. These decisions do
not approve candidate labels or profile release. Both development and acceptance
candidate labels still require actual review. Source review also corrected two
Workit draft questions and a contract-navigation evidence alternative; fresh
collection uses the corrected Unicode-verified dataset. An intermediate draft
with damaged Vietnamese codepoints and its interrupted run were invalidated,
preserved privately and excluded from evidence.

### Reviewed lexical enrichment v2 - 2026-11-16

After explicit approval of all three review lots, lexical v2 was provisioned at
`artifacts/semantics/v2/lexicon.json` with its provenance manifest. It adds **56
aliases across 53 concepts**, retaining all 232 concepts, canonical phrases,
prior aliases and guards. Active rewrite rules increased from 171 to 227. V1
remains byte-identical; this task did not change source packages, runtime code,
models, database, environment values or services.

Preparation passed 504 new-alias behavior checks, nine additional client cases,
1,272 inherited direct/paraphrase checks and 245 old-alias examples. Focused
semantic/preparation/inventory tests passed **72 tests**, with one dependency
deprecation warning, before provision. After provision, runtime inventory
verification passed for **20 assets** and the package-bound loader accepted v2.
These are lexical checks, not retrieval-quality acceptance. The manifest keeps
evaluation pending and supplies no relevance profile. Previously exposed cases
remain regression data. See [lexical enrichment](docs/vietnamese-semantics.md).

### Accepted rerank labels and full benchmark - 2026-11-16

The user explicitly accepted all three final review scopes for **145 cases /
4,350 candidate labels**, including the 20 remaining passage decisions and 46
unsupported-in-current-corpus hypotheses. The accepted cases were frozen and a
full real-model benchmark completed against the isolated schema-0006 database's
28-package / 524-chunk corpus. Newer source/semantic workspace edits were
preserved: evaluation used hash-verified approved source bytes and the unchanged
current runtime in an isolated workspace, not an older implementation.

The benchmark **failed evidence and relevance gates**: complete positive recall
was **52/75 (69.33%)** overall, **17/24 (70.83%)** for calibration and **14/22
(63.64%)** for holdout. Holdout also returned irrelevant passages. There was one
candidate-pool miss and 23 post-filter empty positive cases. The 100-request
latency gate passed with p50 **2.259 s** and p95 **4.257 s**; database/package
before/after checks passed. No critical or required-top-1 cases were designated
in the accepted scope; a vacuous top-1 gate is not coverage of those requirements.

No accepted relevance profile was emitted or promoted, and the requested final
artifact remains outstanding. Fixed-score analysis shows that changing only the
scalar threshold cannot meet the accepted quality gates. Any outcome-driven
scoring development needs a separately reviewed fresh holdout. The private
acceptance, frozen inputs, execution receipt, full report and diagnostic are in
`.tmp/relevance-profile-20261116-05/`. Existing runtime inventory verification
passed for **18 assets**. This operational run changed no runtime code, model,
environment configuration or API deployment; application tests were not rerun.
See [calibration acceptance](docs/online-reranking.md#evaluation-and-rollout).

### Reviewed lexicon provisioned by explicit request - 2026-11-16

The user explicitly requested placing the reviewed lexicon at
`artifacts/semantics/v1/lexicon.json` before retrieval evaluation is complete.
The file is byte-identical to the privately exported, source-approved lexicon;
its provenance manifest records `source_reviewed_provisioned_evaluation_pending`
and `runtime_release_accepted: false`. Both files are in the runtime inventory.
Verification passed for **18 assets**, and the package-bound semantic loader
accepted the provisioned file. No matching relevance profile is supplied, and
this storage step does not establish retrieval quality. `.env`, database and
services are unchanged. See [runtime artifacts](docs/runtime-artifacts.md).

### Semantic source reconciliation and grouped review - 2026-11-16

The user-authorized source preparation restored the labor inpatient clause and
added a separately attributed conversation-clarification package. Original PDF
and extraction bytes are unchanged. Five affected/new packages passed structural
validation and local E5 token preflight. The private proposal covers 29 packages /
530 chunks with 232 concepts and 636 evidence units. The user explicitly approved
all three source/alias groups; a reviewed lexicon was exported privately and
passed the real package-bound loader and bounded-rewrite smoke checks. This is
source acceptance, not runtime release. See [source reconciliation](docs/vietnamese-semantics.md).

Focused package/preparation/semantic-runtime tests: **86 passed**, with one
dependency deprecation warning. Initial sandbox runs encountered Windows
temporary-directory access errors; the passing run used a fresh repository-local
extended-length temporary directory outside the sandbox. Existing runtime asset
verification passed for all 16 files. These checks establish neither source
approval nor retrieval quality.

Read-only inspection of the configured application target still returned
`0005_structured_evidence`, with five target tables and two target columns absent.
Per the latest user instruction, no migration or backup was created or executed.
No publication, full semantic benchmark, accepted artifact promotion, environment
edit or service activation was performed. A refreshed 1,261-case exposed
regression proposal remains unaccepted for evaluation; independent splits and
complete candidate labels are still required. The separately provisioned calibration
database described below is a distinct target; its existence does not establish
compatibility of the configured application connection.

### Isolated v0006 corpus and fresh calibration collection - 2026-11-16

Explicitly authorized provisioning created a separate calibration database at
`0006_publication_baseline`. Read-only schema introspection verified 14 application
tables and ORM/key/index/trigger alignment; it did not execute destructive
constraint tests. Publication and deep readback passed for **28 packages / 524
chunks**, 28 successful attempts, 28 metadata audits and normalized evidence
links. The original v0005 database retained its schema, row counts and ID hashes;
`.env`, source packages, model files and runtime code were unchanged by this task.

A fresh **145-question / 4,350-candidate-pair** collection completed on the new
corpus with matching package/model/corpus hashes. Its 100 warmed requests measured
**p95 3.54 seconds**; collection is not full calibration or relevance acceptance.
Both independent source reviewers covered every returned UUID. They agreed on
122 positive and 4,129 negative proposals, with 99 pairs sent to post-independent
source consultation. That consultation proposed 53 positive, 26 negative and
20 unresolved decisions; it is not a third blind vote. The complete private
source/split/label packet is ready for human review, including 46 unresolved
policy-absence hypotheses. Case support, split applicability and all human
review declarations remain unaccepted. No full benchmark or runtime profile was
issued, and the API connection was not changed. The new run/chunk identities and
metadata-bound corpus fingerprint cannot reuse old-database candidate labels.

An initial pre-attempt rejection was checked to have no attempt/run before
resuming unpublished packages. A generated receipt also contained a pinned-file
flag not exposed by the shared CLI; execution omitted only the verified implicit
default pair, preserving the receipt and effective configuration. See
[receipt execution](docs/online-reranking.md#prepare-baseline-calibration-inputs).
Runtime asset integrity, prepared-input validation and the documented PowerShell
normalization/parser check passed. Application regression tests, original-PDF
fidelity, independent backup/restore and API rollout were not verified by this
data/provisioning task. Private receipts and review evidence remain under `.tmp/`.

### Pre-production database baseline and publication tracing - 2026-11-16

Implemented on the working tree initially based on `782b4f4`; a concurrent
commit, `2c93057`, captured the initial implementation while this work continued.
Existing README/runtime-artifact preparation changes were preserved. The target
is the **new empty-database** `0006_publication_baseline` with 14 application
tables, same-run normalized evidence links, required publication identity,
append-only metadata audit and separate execution attempts/commit receipts.
See [database design](docs/database-design.md) for the data dictionary, invariant
ownership, fresh initialization, unknown-outcome reconciliation and corpus
fingerprint version 2. Package v2/v3 and HTTP contracts remain unchanged; old
corpus fingerprints are not automatically accepted.

Final post-edit non-integration regression passed **813 tests, 1 skipped,
50 deselected**, with the existing Starlette/AnyIO deprecation warning. The
focused schema/publication/storage/evaluator run passed **144 tests** before the
last server-clock, failure-ID and fingerprint-reuse refinements; those changes
are included in the final full run. An earlier full run also passed 813 tests.
Initial development checks exposed an offline SQL guard/import-boundary problem
and two test assertions coupled to insertion order/DDL whitespace; these were
corrected before the passing runs. Sandbox test attempts encountered Windows
temporary-directory access failures; passing runs used fresh repository-local
extended-length `.tmp/` paths outside the sandbox without deleting old trees or
changing permissions.

CLI help and local documentation file-link/whitespace checks passed. The
PostgreSQL suites were **not executed**: credential-safe configuration comparison
found `TEST_DATABASE_URL` was not separate from the application/migration
identity. New integration coverage includes direct SQL constraints/triggers,
schema/ORM alignment, audit and link rollback, metadata-bound fingerprints,
concurrent publication and lost-commit acknowledgement reconciliation. The final
lost-ack integration case was added after the full non-integration run; it was
collected only, not executed. Historical migration-preservation assertions remain
on an isolated legacy test schema. Mocked tests/offline DDL do not establish live
PostgreSQL enforcement, query plans, model quality or performance.

No migration, reset, database connection/write, real-model inference, publication,
calibration, service restart or deployment was performed. Existing database,
source packages, model assets, `.env` and private evaluation inputs were retained.


### Calibration collection preflight blocked by schema mismatch - 2026-11-16

The configured database remains at `0005_structured_evidence`, while the current
publication-baseline code requires additional schema. Read-only preflight failed
with SQLSTATE `42703`; the schema audit found missing `ingestion_runs.created_at`,
`evidence_items.parent_id` and five publication/evidence tables. The database
contains 34 runs, 682 chunks and 28 revisions. Migration `0006` requires empty
application tables and is not a conversion for this populated database. No
migration, republication, schema repair or old-code fallback was performed.
The user selected a compatible current-code database/corpus as the prerequisite
for collection; an empty schema alone does not supply compatible publications.

All **16 selected runtime assets** passed integrity verification. A private
**145-case diagnostic draft** applies two source-supported question rewrites
and adds two independently reviewed negative hypotheses. Shared validation and
minimum split counts pass: calibration 24/20, holdout 22/20 and regression 29/30
supported/unsupported proposals. Negative absence and final human acceptance
remain unresolved. The collection receipt is prepared, but no new collection,
candidate-label review, full calibration or profile issuance completed. Existing
`.env`, runtime code, model files, packages and stored publication data were not
changed by this calibration task. Application regression tests were not rerun
for this data/documentation work; source preparation does not establish live
database compatibility or calibrated relevance.

### Agent-assisted calibration preparation - 2026-11-16

Operational preparation on the working tree based on `782b4f4` verified all
**16 selected runtime assets** and read-only compatibility of **28 published
packages**; the corpus fingerprint matched the earlier collection. Two blind
agent reviews assessed **100 questions / 3,000 candidate pairs** independently:
104 pairs had agreed positive proposals, 2,812 agreed negative proposals and
84 remained disagreeing or unresolved. These are source-based proposals, not
human-approved labels or calibration acceptance.

A new private **143-case draft** moves 39 historical or unrepresentative cases
to regression and adds 43 source-backed question proposals. Its collection
structure and minimum split counts pass the shared validators. Both reviewers
flagged partial or premise-correcting evidence for two proposed unsupported
questions; all 30 new negative hypotheses still require human source review.
One patrol question was revised before collection to preserve ambiguous source
wording, with independent source-fidelity checks. See
[agent-assisted review](docs/runtime-artifacts.md).

No new collection, full calibration, profile issuance, runtime/configuration
change, publication write or API restart was performed. Final calibration-input
validation correctly rejects missing candidate labels. Private review packets
and provenance stay under ignored `.tmp/`; original-source transcription
fidelity, human review, independent backup/restore and rollout remain unverified.
Application regression tests were not rerun for this data/documentation work.

### Incremental publication and retained history - 2026-11-16

Implemented on the working tree based on `cf68ce9`, preserving pre-existing
runtime-asset, Git/LFS and documentation changes. Publication now verifies stored
chunks/evidence before skipping canonical-equivalent input, applies explicit
metadata-only updates without another run/chunk set, and reuses compatible
exact-text vectors for changed publications. Legacy runs remain readable without
configuration rewrites. See [publication decisions and compatibility](docs/offline-indexing.md#publication-and-consistency).
Shared evaluation readers verify current metadata and stored runtime content
separately from original publication byte hashes. No API wire or schema change.

Final post-edit non-integration validation passed **782 tests, 1 skipped,
37 deselected**, with the existing Starlette/AnyIO deprecation warning. The
initial focused run passed 78 tests; an earlier broad snapshot passed 780 tests.
The subsequent broad run caught a new evidence-test assertion indexing an
insertion generator; converting it to a list fixed the test, and the final full
run passed. CLI help, 167 local Markdown file links and diff whitespace checks
passed, including this verification entry. The system Python lacked pytest; tests
used the existing `env` interpreter. An initial sandbox attempt hit Windows
temporary-directory access errors; successful runs used fresh repository-local
extended-length temporary directories outside the sandbox, without deleting old
trees or changing permissions:

```powershell
.\env\Scripts\python.exe -B -m pytest -m 'not integration' -q --tb=short --basetemp '\\?\D:\sources\rita-id\rita-vo-bot\.tmp\publication-incremental-full-03' -o cache_dir=.tmp/publication-full-cache-03
```

Choose a new unused temporary directory when repeating. Integration coverage was
added for metadata-only/history preservation, incremental encoding, rollback,
and the real HTTP-to-storage retrieval path excluding inactive/superseded runs.
These PostgreSQL tests were **not executed**: credential-safe configuration
inspection found `TEST_DATABASE_URL` matched an application/migration database.
The preflight stopped before connecting. A separate disposable database is still
required; mocked tests do not establish live PostgreSQL behavior. No migration,
publication, database write, model inference, service restart or deployment was
performed. Existing data, package files, `.env` and the exported mock CSV were
not changed.

### Selective runtime assets and Git LFS - 2026-11-16

Implementation on the working tree based on `cf68ce9` opens only the pinned
model inventory and selected versioned runtime JSON. Large model/tokenizer
files use Git LFS; all selected model/artifact bytes bypass newline conversion.
The read-only `python -m scripts.verify_runtime_assets` checks the manifest,
hydration, hashes, E5 file inventory and tracked-path policy without optional
ML dependencies, `.env`, network or database access. See
[runtime asset checkout and verification](docs/runtime-artifacts.md#git-lfs-checkout-and-verification).

Post-edit asset verification passed for **16 files / 634,066,120 bytes**;
the baseline file inventory and E5 recursive fingerprint remain unchanged.
The new focused suite passed **25 tests**, including real local Git/LFS
staging, pointer rejection, checkout and fresh-clone hydration with fictional
files and `core.autocrlf=true`. The full non-integration suite passed
**748 tests, 1 skipped, 34 deselected**, with the existing Starlette/AnyIO
deprecation warning. CLI help, 99 local documentation file links and diff
whitespace checks passed. Repository-local LFS filters/hooks were initialized.

Initial sandbox test attempts failed on Windows temporary-directory permissions;
successful runs used fresh repository-local extended-length paths outside the
sandbox without deleting old trees or changing permissions. Intermediate LFS
fixture failures (missing HEAD and absolute extended-length clone arguments)
were corrected before the successful runs. The full suite used:

```powershell
python -B -m pytest -m 'not integration' -q --tb=line --basetemp '\\?\D:\sources\rita-id\rita-vo-bot\.tmp\runtime-assets-20261116\full-01' -o cache_dir=.tmp/runtime-assets-20261116/full-01-cache
```

Choose a new unused temporary directory when repeating. Real model files
remain unchanged; no profile is supplied or feature enabled by this work.
No remote upload, application-repository commit, database operation, training,
calibration or service restart is part of this change. Remote LFS availability,
quota and a checkout of the actual committed model release remain unverified.

### Baseline rerank input preparation - 2026-11-14

Implemented on the working tree based on `b00cdbf`, preserving existing semantic,
intent, API and documentation edits. `python -m src.cli.prepare_rerank` now
imports draft manifests/cases, validates shared evaluator rules and freezes
reviewed label sets against a new-format baseline collection. See
[the preparation workflow](docs/online-reranking.md#prepare-baseline-calibration-inputs).
No runtime API, `.env`, service, publication or calibration quality gate changed.

Post-edit full non-integration validation passed **723 tests, 1 skipped,
34 deselected** before the final missing-input exit-code refinement; the final
focused preparation/evaluator/reranking/diagnostic run passed **93 tests**.
Static checks passed for 88 local documentation file links and diff whitespace.
The existing Starlette/AnyIO deprecation warning remains.
The initial sandbox run hit WinError 5 in pytest temporary storage; successful
runs used fresh repository-local extended-length temporary directories outside
the sandbox. No old temporary trees were deleted or permissions changed.

An actual model-free import copied **28 package-pinned datasets / 100 draft
questions** from the existing storage-calibration manifest to ignored
`.tmp/rerank/`, preserving source files. Collection-stage validation passes;
calibration-stage validation correctly rejects incomplete candidate labels.
All 141 recorded source input hashes remain unchanged. Review declarations
remain false. Import and help checks do not establish
source-label quality, split independence, database compatibility or acceptance.
No real-model collection/calibration, database integration test, profile issuance,
schema migration or API restart was performed by this task.

### Corpus-wide Vietnamese semantics - 2026-11-14

Implementation on the initially clean working tree based on `b00cdbf` adds
model-free corpus inventory/review-gated lexicon export, opt-in bounded semantic
query expansion, optional style-only intent assistance and a paired cross-document
evaluation/calibration tool. Defaults remain off; no real lexicon, classifier or
profile has been accepted or deployed by this task. The public HTTP request and
response are unchanged. See [semantic workflow](docs/vietnamese-semantics.md).

An actual inventory run read **28 packages / 524 chunks**, including source
snapshots and source references, without modifying packages or database data.
Inventory coverage does not certify that every business clause has been
decomposed, that proposed aliases are equivalent, or that evidence is complete.
Agent-authored material remains draft pending independent/source and human review.

The local v3 draft under ignored `.tmp/semantics-v1/` contains **567 proposed
units / 190 concepts**, including additional source-backed branches in labor,
benefits and travel. Independent agent review identified unsafe equivalences;
40 concept alias sets were quarantined as no-op proposals. Structural validation
passes all 524 chunk references, explicitly reports
`semantic_completeness_assessed: false`, and blocks lexicon export until human
review. Source-specific question pairs and review findings are authoring aids,
not an independent reviewed holdout or model-quality evidence.

Final post-edit validation passed **698 tests, 1 skipped, 34 deselected** in the
full non-integration suite after source-reference, strict JSON, profile-promotion
and empty-reason review fixes. The earlier broad run passed 691 tests before
those refinements. The existing Starlette/AnyIO deprecation warning remains. An
initial focused sandbox run encountered WinError 5 in pytest temporary storage;
successful runs used fresh repository-local extended-length paths outside the
sandbox. No existing temporary trees were deleted or permissions changed.

Static checks verified 94 local documentation file links and both new command
help interfaces. These checks do not validate semantic equivalence, live API
compatibility, database behavior or real-model quality.

The local evaluation draft contains **1,139 proposed cases**, including the five
known questions in regression. Collection-shape validation passes; full evaluation
correctly rejects human-review status. Unsupported controls and exhaustive union
candidate labels remain to be authored/reviewed, and corpus-derived wording is
not an independent unseen holdout. See the local-only
`.tmp/semantics-v1/HANDOFF.md` for review artifacts and remaining work; ignored
artifacts require separate retention and are not shipped in a clean clone.

No database integration tests, real-model semantic quality benchmark, classifier
training, schema migration, publication, `.env` change or API restart was performed.
Draft labels/fixtures are not live compatibility or quality acceptance evidence.

### Independent relevance filter switch - 2026-11-14

Implemented on the working tree based on `cfb2e00`, preserving existing diagnostic/documentation changes, the user-owned service whitespace edit and concurrent IDE settings changes. HTTP configuration now supports `RELEVANCE_FILTER_ENABLED` (default `true`) independently of reranking. Disabling it returns unassessed top-K source candidates with an explicit warning and skips profile-file loading; CLI/evaluator/benchmark defaults and calibration gates remain unchanged. See [filter modes](docs/online-reranking.md#local-model-and-configuration).

Post-edit validation: **138 passed** in the focused retrieval/reranking/API suites; **619 passed, 1 skipped, 34 deselected** in the full non-integration suite. The existing Starlette/AnyIO deprecation warning remains. Two initial sandbox attempts encountered WinError 5 in pytest temporary storage; successful runs used fresh extended-length repository-local `.tmp/relevance-switch-*` paths outside the sandbox. No existing temporary directories were deleted or permissions changed. The final diff still contains the pre-existing trailing-whitespace line in `src/retrieval/service.py`; it was preserved rather than cleaned up.

These tests use synthetic model/search boundaries and exercise the HTTP-to-retrieval path. No live API check, real-model quality benchmark, integration database test, migration, publication, profile generation, `.env` edit or service restart was performed. The backend currently drops relevance metadata, so the portal cannot identify unfiltered results; no backend or portal changes are included.


### Empty-retrieval diagnostic and review handoff - 2026-11-14

Post-edit investigation started from clean `cfb2e00`. Added `scripts.diagnose_retrieval`, an offline saved-collection inspector with a metadata diagnostic and a separate private unranked source worksheet; no public API, runtime ranking or profile acceptance behavior changed. See [diagnostic procedure](docs/online-reranking.md) and [utility usage](scripts/README.md#evaluation-and-experiments).

Read-only local-model checks verified **28 eligible publications / 524 active chunks** against package contents and normalized vectors. The three user-reported questions each placed source-backed minimum evidence in the top three before reranking and first after reranking, but all target scores were below the historical failed calibration cutoff. Minimum evidence is not a complete answer, personal entitlement or current-policy acceptance. Native tokenizer/window/ONNX parity passed for four fictional passages / nine windows with identical batching; singleton inference showed quantized score drift and is not a valid same-batch parity comparison. Five high-scoring historical negatives were rechecked against retained extraction excerpts and still concerned different workflows; no existing labels were changed. The other historical labels remain pending user review, not newly accepted by this task.

The new four-case collection (three originals plus a typo comparison) used **100 latency requests**, p50 **2.070 s**, p95 **3.012 s**, and emitted no profile. Before/after application-table row hashes, packages, E5 bytes and `.env` matched, including a final check after HTTP probes. The existing HTTP service returned ready and reproduced `no_matches/relevance_unavailable` for all three originals; its in-memory configuration was not inspected. No document sources, packages or stored data changed; no migrations, publication, model replacement or API restart occurred. Private receipts, the **111-case / 3,330-pair** historical worksheet, **four-case / 120-pair** current worksheet and concrete review/next-scope instructions live under `.tmp/no-matches-diagnostic-20261114/REVIEW.md`. No unchanged full calibration was rerun and no profile was promoted; the current-model quality limitation remains unresolved.

Verification: **75 passed** in the focused diagnostic/reranking/API suites; **586 passed, 1 skipped, 34 deselected** in the full non-integration suite. After final utility provenance/target-package refinements, its **6 tests passed** again; the broad run preceded only those refinements and documentation changes. The existing Starlette/AnyIO warning remains. Initial sandbox attempts failed on WinError 5 in pytest temporary storage; successful runs used fresh repository-local `.tmp/` directories outside the sandbox without changing permissions or deleting prior trees. Initial private diagnostic-harness attempts were corrected before the completed `run-05`; their failed receipts are not passing evidence. Integration tests, a new independent holdout acceptance run and profile rollout were not performed.

### Durable runtime storage and calibration preparation - 2026-11-14

Working tree started at `67cb66d`; concurrent work committed the shared source/configuration/test edits in `c43688f` while this task was running. Existing staged and unstaged changes were preserved. E5 was copied from `.tmp/models/` to `models/embeddings/multilingual-e5-small/<revision>/`: all **10 files / 493,292,828 bytes** matched by relative filename, size and SHA-256 before/after; the original remains intact. Local `.env` now selects the durable E5 directory, leaves the missing relevance profile unconfigured and retains `INTENT_MODE=off`; unrelated parsed settings were preserved. No service was restarted. E5 loaded on CPU and produced a normalized 384-dimensional fictional-query embedding; the pinned ONNX/tokenizer startup smoke passed. Receipts live under ignored `artifacts/storage-preparation-20261114/`. Independent backup and restore remain **unverified**, pending a user-selected separate destination.

Post-edit checks: **156 passed** in the focused API/IDE/retrieval/reranking/evaluator suites; **580 passed, 1 skipped, 34 deselected** in the full non-integration suite, with the existing Starlette/AnyIO deprecation warning. Successful runs used fresh extended-length `.tmp/storage-20261114-tests-*` paths outside the sandbox. The first focused attempt hit WinError 5 in pytest temporary storage; an initial full attempt hit an import error while an independently edited module was being completed. Both are recorded limitations of those attempts, not passing runs. No permissions were changed or existing temporary trees deleted.

Read-only PostgreSQL checks confirmed **28 eligible active publications / 524 chunks**, package/stored-content/vector identity, and the unchanged relocated E5 fingerprint. A separate new 100-question draft spanning all 28 packages is under `.tmp/storage-calibration-20261114/`, with package-pinned cases, source-extraction excerpts and a user-review checklist. Collection completed with **3,000 candidate pairs**, matching before/after corpus snapshots and unchanged database/package checks; no profile was emitted. It is **not user-approved**, candidate labels are incomplete, and it does not authorize a full benchmark or rollout. The handoff also supplies the independent task's **111 questions / 3,330 existing label proposals** in an unranked source worksheet, after verifying frozen hashes and label mappings; no approval was inferred. Collection-only output is separate from the completed calibration reported below. Static checks passed for 53 local links/anchors, staged/unstaged whitespace and ignored local artifacts. No accepted profile, intent training, schema migration, publication or API restart was performed by this task. See [artifact operations and review handoff](docs/runtime-artifacts.md).

### Relevance calibration - 2026-11-14

Real local-model calibration against **28 current active publications / 524 chunks** failed the [quality gates](docs/online-reranking.md#evaluation-and-rollout). The frozen dataset contains 111 source-reviewed cases: 20 supported and 20 unsupported cases each for calibration and holdout, plus 31 supported regressions, with 3,330 passage labels. Unfiltered top-5 retrieval covered 71/71 supported cases; filtering retained complete evidence for only 20/71, including 0/2 critical leave cases. Supported complete recall was **30% for calibration and 20% for holdout**, below the required 90%. The minimum threshold excluding all reviewed calibration negatives was 7.871049404144288; lowering it would admit irrelevant passages, while raising it cannot recover recall.

The 100 warm latency requests passed the latency gate (p50 **2.008 s**, p95 **3.751 s**), but overall rollout readiness is false. Frozen labels, corpus/model fingerprints, database state and package files passed the run's consistency checks. Private evidence is retained under `.tmp/relevance-calibration/20261114-01/`, including `manifest.json`, `passage-label-audit.json`, `label-freeze.json`, `calibration.json` and `outcome.json`. Labels were reviewed against retained extraction/chunks; this is not a new full-PDF audit or business acceptance.

**No relevance profile was generated or promoted, and this calibration task did not write `.env`.** Independent concurrent configuration/model-path/source edits were preserved; the relocated embedding model's bytes matched preflight. This task did not change runtime code, publish documents, migrate the database, restart the API or run unit/integration suites. The separate suite results below retain their own scope.

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

### Stage 2 batch CLI verification — 2026-11-13

Working tree based on `e656991`, after adding the shared offline preparation CLI: **537 passed, 1 skipped, 34 deselected** in the full non-integration suite, with the existing Starlette/AnyIO deprecation warning. After the final run-directory ownership/cleanup adjustment, all **20 batch CLI tests passed** again. Tests used the isolated extended-length `.tmp/intent-test-runs` workspace outside the sandbox. Coverage includes no-ML imports/help, aggregated validation, review-before-model gating, token-only preflight, legacy/new artifact parity, unique run directories, atomic manifests, interruptions, changed inputs and failure reporting. `pip check`, legacy/new CLI help and scoped diff checks passed.

Actual CLI validation accepted the structure of the 600-case draft while reporting `review_required: true` and `training_allowed: false`. Real local CPU E5 preflight checked all 600 questions without encoding; none exceeded the token limit. The report is under `.tmp/intent-v1/preflight-cli-report.json`. An actual `run` invocation correctly stopped before model loading with `human_review_required`, leaving a diagnostic manifest and no candidate. No reviewed training, semantic quality evaluation, database benchmark, accepted artifact or observation enablement was performed. Existing dataset/chunk changes and `.env` were preserved.

### Intent verification — 2026-11-13

Implementation working tree based on `e656991`, preserving existing documentation, corpus and answer-evaluation changes. Full non-integration verification after implementation: **516 passed, 1 skipped, 34 deselected**, with one existing Starlette/AnyIO deprecation warning. After final dataset-validation/diagnostic hardening and one additional test, the focused intent suites passed **35 tests**. Tests used a separate extended-length `.tmp/intent-test-runs` directory outside the sandbox; the first sandbox attempt failed at pre-existing temporary-directory permissions. No permissions or existing temporary trees were changed. Synthetic tests cover NumPy/sklearn parity, training/holdout separation, acceptance gates, off/observe retrieval invariance, HTTP/CLI wiring, encode counts, cancellation and cleanup. CLI help and `pip check` passed.

The private draft `.tmp/intent-v1/draft-cases.json` contains 600 structurally checked, **unreviewed** cases; training rejects it before loading E5. Human label/split review, real-model intent training, the quality gate and paired retrieval latency benchmark have **not run**. No accepted intent artifact has been produced, and observation has not been enabled. Database integration tests were deselected; no migration, publication, source-package edit, backend or portal change is part of this implementation. See the guide for the draft's template/diversity limitations and the independent retrieval calibration prerequisite.

<a id="offline-answer-evaluation-endpoint-not-implemented"></a>

### Fallback verification - 2026-11-13

Implementation working tree, including pre-existing reranking changes: the public function/CLI/HTTP path now filters each passage before top-K and returns a required nullable `fallback_reason`. Uncalibrated vector-only/reranked candidates are withheld. See [the response and rollout contract](docs/online-reranking.md#retrieval-and-response-semantics). No source packages, publications, schema, model files or application `.env` were changed.

Non-integration verification: **454 passed, 1 skipped, 34 deselected** using an isolated extended-length `.tmp/` basetemp outside the Windows sandbox. Earlier sandbox runs hit access-denied temporary-directory errors; no permissions were changed. The new per-passage 100-question source-labeled benchmark has **not been completed**: the available historical 40-question set lacks per-candidate labels and has related intents across splits. No schema-v2 profile was emitted, and no real-model quality/latency gate is claimed. Subsequent focused checks passed: 62 retrieval/API tests after profile hardening, then 24 reranking/benchmark tests after adding two orchestration cases. The pinned local ONNX smoke passed on two fictional passages without database access. PostgreSQL integration tests were not executed; synthetic tests do not verify the new corpus snapshot query on a real database.

### Online reranking verification — 2026-11-13

Historical top-1 evaluation, predating per-passage filtering (not a current rollout approval). Executed on the implementation working tree with the downloaded ONNX verified against official revision `1427fd652930e4ba29e8149678df786c240d8825` and its matching local tokenizer. A read-only, package-pinned 40-question evaluation (30 supported, 10 unsupported; known prompts kept as regression) found complete top-5 evidence for all 30 supported cases, with no candidate-pool misses. The required bereavement-family top-1 case failed: its detail ranked fourth. The calibrated relevance gate also failed (holdout positive top-1 recall 75%; calibration positive top-1 recall 46.7%; no holdout negative acceptances at that threshold). No calibrated profile was emitted; reranking remains disabled by default.

On 100 sequential post-warm-up requests with 30 candidates and batch size 4, p50 was 2.59 s and p95 was 3.88 s. Peak process working set was 1,951,526,912 bytes (about 1.82 GiB), including E5 and ONNX. Host: Windows x86-64, Intel Family 6 Model 165, Python 3.14.10. These observations are not a deployment SLA or evidence of complete policy coverage. Selected packages/storage passed before/after read-only verification. Local reports/cases are under ignored `.tmp/online-evaluation/`; private questions and source contents are not committed. The benchmark preceded final profile-fingerprint hardening (batch/runtime identity), which does not change candidate selection or inference; its profile fingerprint is intentionally not reusable by the final implementation.

Final non-integration verification: `python -m pytest -m 'not integration' -q` with a fresh extended-length `.tmp/` basetemp on Windows: **442 passed, 1 skipped, 34 deselected**. An initial ordinary-path run encountered 17 legacy asset-restructuring fixture errors from Windows path length; those resolved with the documented extended-length path, without changing the fixtures. Focused retrieval/API tests and the real local ONNX smoke passed. The updated PostgreSQL integration cases were **not executed**: automatic approval review rejected migrations/writes against the configured test URL because disposable ownership had not been verified. Read-only evaluation does not replace those transaction/eligibility fixture tests. No publication, schema change or stored-vector update was performed.

## Setup (PowerShell)

Provision a separate empty PostgreSQL database for the current schema. Do not point the new publisher at an older database; see [initialization and boundaries](docs/database-design.md#initialization-and-boundaries).

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

The annotated [environment example](.env.example) documents active settings. HTTP startup gives root `.env` precedence over launcher values; standalone entrypoints retain their loading policy. Restart to apply configuration. Intent/filter/profile env keys are retired and ignored, with name-only warnings if inherited. See [current catalog workflow](docs/catalog-answering.md).

Runtime model files use durable storage under `models/embeddings/` and the existing root reranker layout. A selective Git allowlist and Git LFS preserve the pinned model inventory; the selected current runtime JSON and provenance can be tracked at stable paths under `artifacts/`. Git retains prior versions; see the [release and rollback rules](docs/runtime-artifacts.md#release-recovery-and-rollback) before restarting an older configuration. After checkout, install Git LFS, run `git lfs pull`, then `python -m scripts.verify_runtime_assets` before model startup. `.tmp/`, download caches, experiment reports and machine receipts remain ignored. No accepted profile is included merely by enabling this storage policy. See [runtime artifacts](docs/runtime-artifacts.md) for checkout, artifact promotion, verified copying and independent backup/restore. Do not clear old temporary model copies until validation and recovery are complete.

Configuration presentation verification (2026-11-13, existing working tree): parsed `.env` values were identical before and after the comment/layout update; `.env.example` only removed the active sample profile assignment by commenting it out. Parser checks found no malformed entries or duplicate active keys. No API, database, model benchmark or application test suite was run for this documentation-only change.

Before running Alembic on a fresh database, complete the [database provisioning procedure](src/database/README.md#fresh-database-provisioning): create the target database, install pgvector on the PostgreSQL server, and have a provisioning role/DBA enable `vector` in that database. This is required even if the migration account could enable it: the baseline uses vector columns before the later migration's extension statement. Installing the Python package does not enable the server extension.

Fill in `DATABASE_URL` in `.env` (and optionally `MIGRATION_DATABASE_URL` for a separate DDL account) before running from the project root with the virtual environment activated:

```powershell
python -m alembic upgrade head
python -m src.cli.check_db
```

`upgrade head` runs all pending revisions in dependency order, not only the final script. On a fresh database missing `vector`, it fails in the baseline before the guard revision can enable the extension. See [existing database upgrades](src/database/README.md#existing-database-upgrades) for inspection, backup and worker shutdown requirements; do not reset, stamp or downgrade the application database to test this procedure.

## Local question retrieval

Use `python -m src.cli.retrieve_documents --question "Synthetic policy question" --model-path <local-model> --model-revision <40-hex> --top-k 5 --json`. The CLI uses the same candidate policy as HTTP: at most top-K unassessed excerpts, with provenance and explicit warnings. It never generates an answer or certifies relevance/completeness. Model token limits, eligible current publications, E5 compatibility and safe failures remain enforced.

Optional `--rerank`, `--rerank-model-path`, `--rerank-candidates`, `--rerank-batch-size`, `--semantic-mode` and `--semantic-artifact-path` configure local ranking/expansion. Intent/profile arguments have been removed. See [command inventory](src/cli/README.md) and [manual evaluation](docs/retrieval-evaluation.md).

## Local retrieval API and Swagger

Install `requirements-dev.txt` and `requirements-example.txt` in the Python 3.14 repository virtual environment. Retrieval loads E5-small directly in Python on CPU and uses no generative model; the optional answer route renders source-bound templates without a generative model or native GGUF binding. The application never downloads weights or falls back to hosted inference.

Use `.env.example` for `API_HOST`, `API_PORT`, `E5_MODEL_PATH`, `E5_MODEL_REVISION` and `RETRIEVAL_TOP_K` (default 5, range 1-20). For HTTP startup, repository `.env` values override process environment; missing keys use process environment, then defaults. This also applies to database URLs. Restart the API after changes; configuration is not hot-reloaded. Relative model paths resolve from the repository root. The default pinned E5 directory under `models/embeddings` must exist and match the indexed run. Model files must remain immutable until shutdown. PostgreSQL, an explicitly migrated schema and already-published public documents are prerequisites; startup does not migrate or publish.

```powershell
.\env\Scripts\python.exe -m src.cli.serve_api
```

Open http://127.0.0.1:8000/docs and call `POST /api/v1/retrieve`:

```json
{"text":"What eligibility conditions apply?","top_k":5}
```

`text` must be a nonempty string after trimming, at most 8,000 characters and within E5's token limit including the query prefix; input is not truncated. Optional `top_k` must be an integer from 1 to 20; omitting it uses `RETRIEVAL_TOP_K`. Null, boolean, string and out-of-range values are rejected, as are extra fields.

The response contains `status` (`matches_found`, `no_eligible_documents`, `no_matches`), `matches`, required nullable `fallback_reason`, `relevance` and `timings` (`retrieval_seconds`, `total_seconds`, `rerank_seconds`). Each match retains content, chunk/index/run/document/revision IDs, title, relative source path, version label, applicability, effective dates, source spans, author ID/role and cosine distance. No vectors, generated answer, confidence or evidence-completeness assessment are returned. Additive relevance metadata and optional raw reranker scores are described in [online reranking](docs/online-reranking.md). Internal vector candidates sort by smaller distances; enabled reranking sorts them by raw reranker scores before top-K selection. Neither score is confidence; all candidates remain unassessed. Exact catalog evidence checks independently control template answers. The API cannot count benefits, resolve personal eligibility or determine whether top-K covers an exhaustive request.

This is a breaking replacement: `POST /api/v1/ask` no longer exists and returns 404. Clients must use the new route and response. Old `ANSWER_TOP_K` and `OLLAMA_*` settings are unused; configure `RETRIEVAL_TOP_K` instead. Removing the project dependency does not uninstall software or delete model weights on the machine.

Use the supplied one-worker loopback entrypoint on the psycopg-compatible Windows loop. Every HTTP deployment requires CHAT_BOT_SERVICE_KEY, including Type A without conversation/HRM. All /api/v1 routes and readiness probes require that shared header; Backend still authorizes user/HRM access. See [service authentication](docs/http-service-auth.md) and [Type B](docs/type-b-http.md). Startup loads E5/checks DB; one inference owns the busy gate and competitors receive 429. The 120-second deadline still drains active workers before release, so cancellation/shutdown may exceed it.

`/health/live` reports HTTP availability. `/health/ready` checks initialized model state and database connectivity, not publication completeness, model compatibility with every stored run or semantic quality. Shutdown drains active work and closes E5, the optional reranker and the shared database. Imports create no runtime connections. Questions and document contents are not logged or retained as history.

Invalid input returns 422, busy 429, unavailable dependencies/model mismatch 503 and timeout 504. Empty valid retrieval returns 200; operational failures never become empty evidence. See [manual retrieval evaluation](docs/retrieval-evaluation.md) for read-only real-model checks.

## Optional online CPU reranking

See [online reranking](docs/online-reranking.md) for the pinned local ONNX model, tokenizer provisioning, configuration, response changes, read-only evaluation and rollout gates. This is disabled by default and requires no rechunking, embedding or database migration.

## Optional intent observation

The classifier and its offline tools/kits are retired. Catalog semantics and bounded Type B tasks replace intent heads. No intent artifact or relevance profile is required. See [current design](docs/catalog-answering.md). Historical intent verification is retained in earlier dated entries.

## Grounded answers and offline acceptance

Enable `ANSWER_ENABLED` only with a schema-v2 lexicon/catalog and provisioned retrieval dependencies. The source-bound template API reads exact linked evidence from eligible DB publications, independently of search top-K. Unknown tasks ask for detail; explicit document lookup may return unassessed excerpts. There is no generator, verifier, learned intent model or relevance profile.

Use `python -m src.cli.build_artifacts impact`, then deliberate `refresh`, `build`, `check` and `python -m src.cli.check_answer_catalog`. These commands do not publish to the DB. See [answer contract and source updates](docs/catalog-answering.md) and [verification](docs/answer-evaluation.md). Historic model/profile studies in dated entries are not current acceptance evidence.

## Retrieval evaluation

Use `python -m scripts.evaluate_retrieval --package chunks/<document_id> --cases .tmp/evaluation/cases.json --model-path <local-model-directory> --model-revision <40-hex-commit> --top-k 5 --output .tmp/evaluation/report.json` for read-only evaluation of a published package. Package and cases paths are required; top-K defaults to 5 (1–20). Reports may only be written under `.tmp/`, outside the package/model and without overwriting the case file. The evaluator reads revision identity from metadata and rejects stale runtime-content/model fingerprints, current metadata/access/relation mismatches, ineligible revisions, changed inputs or stored content/spans/vectors/evidence that differ from the selected package. Original run byte hashes may precede harmless formatting or explicit metadata-only updates; they are not treated as current runtime identity. It records ranks, distances, model-load/overall question timings and storage verification, without copying retrieved content or vectors into reports. These timings do not separate query embedding from database latency.

Author cases before examining retrieval rankings. A case file contains `revision_id`, `chunks_sha256` (the `canonical_hash` of the validated payload's `chunks` returned by `src.ingestion.document_packages.load_package`) and a nonempty `cases` array. Each case has a unique string `id`, `question`, `category` (`detail`, `condition`, `aggregate`, or `unsupported`), optional boolean `critical`, and `required_groups`: a list of nonempty lists of zero-based package chunk indices. Every group must have at least one retrieved member from the pinned revision/run. For example, `[[1, 2], [3]]` requires chunk 1 or 2 plus chunk 3. Choose chunks containing the required answer evidence, not merely a shared source segment; an inventory can reference a detail's segment without containing its amount. Unsupported cases may have empty groups and are marked for manual review, never scored as answered or correctly refused. Keep real-document case files and reports under ignored `.tmp/`; committed tests use synthetic content.

The target is at least 90% complete evidence among detail/condition cases with every marked supported critical case passing. Aggregate results are reported separately. Exit codes are 0 for meeting this target, 1 for missing it, and 2 for evaluation/input/storage failures (argument errors also use 2). An unsupported-only set cannot meet the target. This evaluates evidence retrieval, not generated answers or fallback behavior. Agent review must still assess ambiguous wording, missing operational facts and semantic sufficiency. Package changes require re-pinning cases and a matching publication; retain baseline reports and disclose any tuning on the evaluation questions.

## HTTP service operation and rollout

FastAPI is the only service transport. Run `python -m src.cli.serve_api` with the
repository virtual environment activated; see [HTTP setup](#local-retrieval-api-and-swagger)
for model, database and configuration prerequisites. The backend continues to
call `POST /api/v1/retrieve`. Request/response contracts are unchanged; the answer
route remains separately opt-in. No migrations or publication are part of this
transport cleanup.

The former health-only gRPC server and `serve_grpc` entrypoint are removed without
a compatibility stub. Remove any old launcher and `GRPC_*` environment entries;
leftover variables are ignored. Removing direct dependencies does not uninstall
packages from an existing virtual environment; prefer a fresh environment built
from the committed requirements. Do not force-remove transitive dependencies.

Use these HTTP probes (substitute the configured port when needed):

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/health/live -TimeoutSec 5
Invoke-RestMethod -Uri http://127.0.0.1:8000/health/ready -Headers @{ 'X-Rita-Service-Key' = $env:CHAT_BOT_SERVICE_KEY } -TimeoutSec 20
```

Liveness reports HTTP availability. Readiness checks initialized retrieval/model
state and database connectivity; it does not certify catalog availability,
retrieval quality or publication completeness. Allow startup time for local model
loading and the database check before enforcing probes.

For deployments that require answers, also call
`GET http://127.0.0.1:8000/health/answer` with the service-key header and a 20-second client timeout. It returns
200 with `{"status":"ready","reason":null}` only when answer initialization and
retrieval/database readiness pass; disabled answers or unavailable dependencies
return 503 with a fixed reason. This probe does not run a question, reload a
catalog or acquire the request busy slot. See [answer diagnostics and reason
codes](docs/answer-evaluation.md#configuration-and-validation).

When updating to the `.env`-first HTTP configuration policy, compare the file
with launcher configuration before restarting: declared database URLs are also
overridden. Keep `.env` private and do not copy credentials into diagnostics.
After an explicit restart, check startup switch source/value logs, both existing
health probes and `/health/answer`; test one valid answer if enabled/provisioned.
For the original conflict regression, a launcher `ANSWER_ENABLED=false` with
`.env` `ANSWER_ENABLED=true` must use the file value. No launcher changes or live
restart are performed automatically by the application. Rollback requires the
previous artifact and a restart, with its previous environment precedence reviewed.

Before rollout, inventory external service launchers and probes; these are not
provided or verified by this repository. Replace any gRPC probes with HTTP probes
before retiring the old process, then deploy the bot with the existing one-worker
entrypoint and remove the old launcher. Smoke-test both probes, a direct retrieval
and retrieval through the existing backend. Valid empty outcomes with explicit
fallbacks are not transport errors. Test `/api/v1/answer` only when already enabled
and provisioned. Confirm the retired process is stopped and monitor startup
failures, response latency and HTTP 429/503/504. On regression, restore the previous
application artifact and launcher; no data rollback is needed.

Keep Python on `127.0.0.1`. A reverse proxy sharing the bot's network namespace
can expose authenticated HTTPS to a remote Backend; see
[shared HTTP authentication](docs/http-service-auth.md). Backend timeout is a
fixed 60 seconds versus the bot's 120-second deadline.
A caller timeout/disconnect does not guarantee immediate retrieval cancellation;
this existing behavior and the single-request busy limit are unchanged. Backend
and other HTTP clients must send the shared key; portal and HRM-source logic are unchanged.

## Database configuration and behavior

`DATABASE_URL` uses `postgresql+psycopg://user:password@host:5432/database`. URL-encode special characters in usernames and passwords. Configure TLS for the server, for example `?sslmode=verify-full&sslrootcert=C%3A%2Fcerts%2Froot.crt`. Standalone database/CLI entrypoints give system environment precedence over `.env`. HTTP startup instead applies `.env` first, including database URLs; see [HTTP configuration](#local-retrieval-api-and-swagger). Do not commit `.env` or credentials.

`MIGRATION_DATABASE_URL` optionally supplies an account with DDL permissions; when absent or empty, migrations use `DATABASE_URL`. Migrations run only through explicit Alembic commands, not at application startup. Fresh provisioning uses the `bce4184a7f51` baseline after enabling vector. The additive `c7d92e6a104b` guard upgrade/downgrade preserves existing tables/data and does not require them to be empty; baseline downgrade drops application tables and must never run on the application database. Both downgrades preserve the shared `vector` extension. See [database initialization and boundaries](docs/database-design.md#initialization-and-boundaries). Future runtime access should use permissions needed for application tables rather than migration privileges.

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

## Shared memory cache

Install the pinned `aiocache==0.12.3` through `requirements.txt` in the repository
virtual environment. The helper creates no network service, port, database table
or environment setting. FastAPI lifespan owns one `CacheManager` at
`app.state.cache_manager`; consumers resolve it with
`src.helper.cache.get_cache_manager(request)` (also usable with `Depends`), or
receive that instance explicitly in their constructor. Importing the helper does
not create a cache instance. See [architecture](ARCHITECTURE.md#shared-memory-cache)
for ownership and concurrency boundaries.

```python
from src.helper.cache import get_cache_manager

async def example(request):
    cache = get_cache_manager(request)
    await cache.set("example", {"label": "Fictional example"}, namespace="demo")
    return await cache.get("example", namespace="demo")
```

The async API is `get(key, *, namespace, default=None)`,
`set(key, value, *, namespace, ttl=None)`, `delete(key, *, namespace)`,
`clear(*, namespace)` and `close()`. Delete returns whether a live entry existed;
set/clear/close return no value. Missing entries return the supplied default;
cached JSON null remains null even when a different default is supplied.
Keys and namespaces must be nonblank strings and remain distinct. Clear only
affects the exact namespace. Values must be JSON data with string object keys,
finite numbers and valid UTF-8 strings; tuples, ORM objects and arbitrary Python
instances are rejected. Values are serialized on write and decoded on read, so
caller mutations cannot change cached data. Do not cache credentials.

Defaults are a 300-second TTL, 1,024 entries across all namespaces and a maximum
64 KiB UTF-8 JSON value. Constructor overrides are available; no `.env` change
is required. TTL is in seconds, must be positive and finite, and begins on set;
reads do not extend it. `ttl=None` uses the default. Invalid data/limits raise
`ValueError`; an oversized write does not replace or evict existing values.
Operations reclaim expired data, then writes evict the least recently used
entry when full. Expiration is lazy, with no periodic task: an idle cache can
retain expired data until the next operation or close. Limits bound serialized
payloads and entry count, not total process RAM.

The cache is shared within one app/process only, is lost on restart and is not
shared with Backend or other workers. It must be called from its owning event
loop, not worker threads. Use PostgreSQL persistence in future features that
need recovery. The [conversation coordinator](docs/conversations.md) now uses
this helper for structured context; it supplies compaction/restoration separately.
Retrieval results, full answer responses and HRM facts are not cached.

## Testing

Run unit tests (shared database fixtures only execute when requested by integration tests):

```powershell
python -m pytest -m 'not integration'
```

The non-integration suite includes HTTP contract and lifecycle tests using synthetic services, without an external backend or database. Synthetic multi-format package/converter tests check JSON contracts and byte preservation; they do not parse actual PDF/XLSX files or verify agent extraction accuracy. Publication tests are in `src/tests/test_publication.py` and `src/tests/test_publication_integration.py`; the production module retains its existing name.

Pytest stores its cache in `.tmp/pytest-cache` and, by default, test temporary files in `.tmp/pytest-work`. The test configuration creates the temporary root when pytest starts and restores its environment override at shutdown; importing the fixtures does not create directories. Pytest manages numbered session directories and locks so concurrent runs use separate temporary directories. The retention policy keeps the latest three sessions after normal cleanup, including successful tests for debugging. Active, interrupted or permission-blocked sessions can remain longer; this is not a disk-space limit. The entire `.tmp/` workspace is Git-ignored.

Explicit `PYTEST_DEBUG_TEMPROOT` or `--basetemp` overrides are respected. Keep overrides under `.tmp/`; create their parent directories first. Prefer `PYTEST_DEBUG_TEMPROOT` when changing the temporary root while retaining pytest's session numbering and cleanup. Never share a fixed `--basetemp` between concurrent runs. Pytest may delete an existing basetemp directory before running, and explicit basetemp directories do not use the three-session retention policy. Never select a source or package directory.

On Windows, if temporary-directory permissions or long paths prevent pytest from running, use a fresh, writable directory under `.tmp/`. For example, run `New-Item -ItemType Directory -Force .tmp` and select an unused path such as `--basetemp '\\?\D:\sources\rita-id\rita-vo-bot\.tmp\pytest-work-retry-1'`, adjusted for your checkout. Extended-length paths can address long paths but do not bypass permissions. Errors are reported without silently choosing another temporary root. Do not create new `pytest-cache-*` directories at the repository root. Remove obsolete temporary directories only after checking their contents and confirming no test session is using them; leave inaccessible directories for explicit permission troubleshooting.

For integration tests, configure `TEST_DATABASE_URL` to a dedicated disposable PostgreSQL database with pgvector installed on its server, then run:

```powershell
python -m pytest -m integration
```

Integration tests modify the selected database. The fixture validates `TEST_DATABASE_URL` and rejects a match with the configured application or migration host/port/database before running migrations; it cannot detect different hostname aliases for the same server. It upgrades to head twice to check idempotence. Publication/migration tests then require all application tables to be empty; that check occurs **after** the migration, not before it.

Tests write synthetic documents, revisions, access rules, relations, ingestion runs and vector chunks into application tables and delete application-table rows during fixture cleanup. Migration preservation tests downgrade only the additive guards to `bce4184a7f51` and verify unchanged rows after re-upgrade. A separate empty-schema test traverses the destructive baseline only on the disposable database, preserving an unrelated synthetic table and the vector extension. Other tests create/drop randomly named tables or use temporary vector tables. Normal completion leaves application schemas, `alembic_version` and the vector extension in the disposable database. Never use production, the application database or a database containing data you need to keep.

Publication integration tests use synthetic embeddings and a synthetic local model directory to exercise PostgreSQL behavior, including rollback after metadata writes and at chunk insertion. They do not validate a real embedding model or tokenizer.

The fixture provisions `vector` before the baseline; a fresh test database therefore also exercises the extension prerequisite. Reruns retain the existing extension. Missing `TEST_DATABASE_URL` causes integration tests to be skipped; a skipped run does not verify real PostgreSQL connectivity. Do not substitute SQLite for these checks.

Historical source-utility compatibility tests are separate from v2 ingestion. The standalone PowerShell test is not collected by pytest and uses only synthetic temporary sources:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File src/tests/powershell/test-normalize-assets.ps1
```
