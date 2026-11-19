# Rita Bot Architecture

## External stateless HTTP evaluation

`src/evaluation/http_e2e` implements an explicit external-client harness for a
manually started bot; it does not participate in serving. Its CLI separates local
inventory, future SELECT-only HRM/corpus binding preparation and future sequential
socket HTTP execution. Endpoint-specific oracles reuse question-bank expectations
without mutating them; per-profile values and publication prerequisites are frozen
before requests. Conversations and controlled source failures are reported outside
the stateless scope, not silently split or marked passed. Source fingerprint
rechecks, health gating and private reports protect interpretation of results.
Runtime configuration, models, migrations and service processes are unchanged.
Only fictional local harness regressions have run; see the [E2E specification](docs/http-e2e.md)
and [current verification](README.md#current-verification-status).

## Request planning and completeness

Template coverage is recorded at the task and aspect level. The local inventory
and question runner are authoring tools. The runner can use an isolated private
question directory and assert the internal plan's aspects without publishing them
in the HTTP schema. See [coverage and holdout](docs/business-topics.md#template-coverage-and-holdout).

`src/answering/planning.py` analyzes document lookup, clause-local personnel
subjects, direct fields, task candidates and unresolved clauses. Contextual
composition and the HTTP coordinator consume that analysis; evidence selection
and rendering receive the same selected tasks, aspects and document subquery.
`plan_turn` resolves continuations as structured updates to the original request:
task/source choices, platform/type slots, user assertions and active aspects.
It does not concatenate a reply into prose for a second routing pass. The HTTP
coordinator receives the resulting plan directly. Unknown companion requests or
unclear subjects ask for clarification before any personnel/evidence read.
Recognized mixed requests retain both the policy and explicit employee fields.
Named third-party profile requests never fall back to the authenticated subject.

Explicit document lookup bypasses task ambiguity and never implicitly reads
personnel fields mentioned in an instruction. A separately requested self-profile
lookup in the same message is retained and executed only when every part is clear.
General guidance selects a source-bound general route first; registry-owned
platform terms or an explicit system select software instructions. Ordinary policy answers still
require exact eligible authored references, without a vector fallback on unknown
tasks. Generic childbirth wording has two declared policy candidates, rather
than an implicit company-benefit default. Numbered or exact-label conversation
choices retain the original subject, dates, assertions and aspects. Aspect-only
follow-ups replace the active aspect unless the user explicitly requests an
addition. Pending requests survive unrecognized replies until resolved, cancelled
or replaced by a recognized new topic. Ambiguous unaccented owners such as
"Minh/minh" require clarification; accent folding never establishes identity.
Reducer schema 7 validates the shape, size and catalog identity of the one active
request before applying a continuation. An explicit correction can update the
route, and a task change discards inputs bound to the previous task. A dependent
reply to invalid or obsolete context asks for a complete restatement. Stored
schema-2 results and message fingerprints remain intact.

Employee-source startup/connection failures allow a bounded recheck on subsequent
requests needing personnel data: one check at a time, at least 30 seconds between
checks, with a 15-second check deadline. Record validation/conflict failures remain
local to that request and never disable reads for another employee. Disabled sources are never rechecked.
This does not replay an answer or make personnel availability a prerequisite
for document-only readiness. Fixed-code logs distinguish planning, requested
evidence, missing sections/references and dependency failures, without content.

Conversation answer/retry requests have a 120-second cancellation deadline
including reservation, context and completion. Conversation transactions apply
local 5-second lock and 15-second statement timeouts; shared database defaults
are unchanged. Cancellation still drains owned work and finalization. Inspect
the existing message after uncertain outcomes; replay/idempotency and purge
tombstones remain authoritative. See [contextual answers](docs/contextual-answers.md).

## Shared memory cache

`src/helper/cache.py` provides `CacheManager` over the pinned
`aiocache.SimpleMemoryCache`, without a global alias registry. FastAPI lifespan
creates a fresh instance per app startup, exposes it at `app.state.cache_manager`
and clears/closes it on exit, including service factory/start/close failures.
The accessor `get_cache_manager(request)` resolves the current app only and
rejects missing/closed managers. No import-time cache, port or external service
is created. New lifespans and separate app instances never reuse entries.

The helper owns monotonic TTL and LRU metadata together, disabling backend timer
callbacks so expiry cannot leave orphaned metadata. Each operation reclaims
expired entries; no background cleanup task runs. Strict JSON serialization
separates caller values from stored values. Backend keys hash the unambiguous
namespace/key encoding to avoid exposing caller keys in aiocache debug logs;
namespace clearing uses exact manager-owned metadata, not backend prefix clear.
See [API, defaults and usage](README.md#shared-memory-cache).

An async lock serializes individual operations within one event loop. It does
not make a get/modify/set sequence a transaction, coordinate worker processes,
authorize access or prevent duplicate business processing. Consumers must not
call the manager from worker threads or reuse it across event loops. Close is
idempotent; subsequent operations fail. Services can receive the app-owned
instance explicitly in later work; no current answer/retrieval/Type B consumer
uses it directly. The separate conversation coordinator receives the app-owned
instance for structured context; see [durable conversations](docs/conversations.md).

## Durable conversation orchestration

`src/conversation` owns strict wire contracts, authenticated internal routes,
deterministic context reduction and short SQLAlchemy storage transactions.
`src/database/models/conversation.py` registers three chat tables; additive
revision `d8e031c72b19` follows the publication guards. Room identity/authorization
and a durable purge outbox belong to Backend, without cross-database FKs.
The existing `/api/v1/answer` accepts either legacy stateless input or complete
authenticated conversation identity. History/turn/retry/purge routes are separate.

Turn reservations precede inference; atomic completion persists answer, assistant
message, context delta/version and every-eighth-turn checkpoint before caching.
Room advisory locks serialize absent-room/purge and completion races. Startup
marks abandoned processing attempts interrupted; shutdown cancels/drains owned
conversation work before closing engine/database resources. One stopped/drained
old process and one replacement are required. No background inference resumes.

Current database version validates cache hits. Misses rebuild from checkpoint
and completed deltas; reducer/catalog identity prevents stale task inheritance.
Only exact bounded follow-ups inherit context. Every new answer rechecks source
evidence; history and user assertions are not authoritative facts. Purge clears
messages/context/owner and retains only an empty deleted room tombstone.
See [contracts, limitations and rollout](docs/conversations.md).

## Readonly HRM source foundation

`src.type_b.employee_store` owns a lazy independent PostgreSQL/psycopg connection,
fixed 64-column employee_sso_store repository, immutable validated contracts and
service reads by one/batched user_id or employee_id, plus one-hop manager resolution.
The full 64-field source model permits null user IDs for employee-ID reads;
authenticated HTTP identity requirements and the nine exposed facts are unchanged.
The module owns contracts, field registry, normalization, selector, repository,
connection, errors and service; there is no separate source implementation.
HRM_DATABASE_URL never falls back to the
Type A database; schema defaults to public. All source relations are readonly:
dedicated externally provisioned SELECT-only privileges, readonly connection
defaults and REPEATABLE READ / READ ONLY transactions. No ORM/Alembic registration,
source writes, preload, cache, authentication-alias/account access or synchronization is added.
The backend's microsecond timestamp/completeness/employee_id selector is ported,
including inactive winners and conflict failures before normalization.

Manager reads select subjects and targets in one readonly snapshot, using a work-only
projection and at most three queries per batch. Exact manager employee ID wins;
code is a case-sensitive fallback only without ID, with full candidate groups and
representative-code rechecks. Missing, ambiguous, conflicting and self-referencing
links have explicit results; source failures remain errors. Inactive/unlinked
managers retain those properties. Manager relation does not establish approval
authority. Closing rejects new work, drains existing calls and disposes once.
Context requirements, evidence contracts and source-bound composition live under
`src/answering/`; the request coordinator lives in `src/type_b/service.py`.
Backend retains end-user authorization. The employee source remains internal and
readonly. See [employee source](docs/employee-source.md) for the property registry.

## Type B HTTP coordination

One task pipeline resolves documents and the minimum declared personnel projection.
Every HTTP answer/retrieve requires a trusted Backend-derived hrm_user_id. Catalog
schema 3 declares a contextual disposition for each task. Response schema 2 has
one evidence graph for documents, employee facts, user inputs and calculations;
no A/B/mixed response branch remains. Missing HRM never fabricates facts or blocks
otherwise grounded policy. Exact date intervals are distinguished from policy
entitlement. Manager lookup shares the subject's readonly transaction.

Reducer schema 7 stores bounded user assertions and task references. Cache
namespace v2 scopes derived state, and the reducer validates checkpoint schema
and artifact identity before reuse. Portal renders one answer/evidence view.
An explicit CLI handles development chat reset. See the
[contextual contract and reset procedure](docs/contextual-answers.md).

## Command ownership

[Application entrypoints](src/cli/README.md) remain in `src/cli/`; [manual utilities, offline evaluations and experiments](scripts/README.md) remain in `scripts/`. Both inventories distinguish current commands, compatibility aliases, retired stubs and support modules. Existing command paths remain supported. One-shot retrieval lifecycle/timings live in `src.retrieval.runner`, used by the CLI compatibility wrapper and IDE utility; HTTP continues to own its long-lived service lifecycle. Pure model revision validation lives in `src.ingestion.model_identity`, shared by publication, preflight and evaluation without imports between those CLI implementations. Historical asset tools retain their old `app/` paths and are not current setup commands.

## Standalone Colab intent kit

Retired with intent training and the runtime classifier. Git preserves history; private data is untouched. See [replacement](docs/catalog-answering.md).

## Independent relevance scoring Colab kit

Retired with learned scoring/calibration/profile promotion. E5 and raw ONNX reranking remain local candidate ranking tools.

## Artifact source assembly

Cross-domain routing and ordering live in `src/retrieval/policy.py`, sharing the
lexical registry with source-bound answers. Retrieval schema 2 adds declared
families and per-task/aspect bindings; addressed supplementation shares vector
search transactions and eligibility. Reducer schema 7 retains bounded user slots
and includes the selected artifact identities. No SQL migration or personnel-field
expansion is involved. See [cross-domain contracts and evaluation](docs/business-topics.md).

`src.artifact_authoring.sources` owns the standard-library-only declared source
layout, duplicate/path checks and deterministic assembly. The runtime inventory
checker uses that module to permit exact authoring paths outside the runtime
asset inventory, without importing retrieval, ML or database libraries. Source
index envelopes use schema 1. The selected answer catalog uses schema 3, while
the lexical and retrieval artifacts use schema 2.

`src.artifact_authoring.workflow` owns explicit offline import/build and invokes
the existing semantic/catalog validators against the current package snapshot;
`src.cli.build_artifacts` is its CLI. Importing these modules performs no IO.

Concepts and cards are authored by ID; coverage is authored by document ID.
Version-1 source indexes retain metadata and ordered references. Assembly computes
the catalog's lexicon byte hash and preserves record and field order. Runtime never
loads source indexes. Generated outputs remain tracked. Source changes require
build/check and separate review of provenance and inventory.
See [authoring and recovery](docs/runtime-artifacts.md#split-source-authoring).

`artifacts/questions` is a separate authored question/oracle dataset. Its explicit
offline runner lives in `src/evaluation`, with a CLI in `src/cli`; serving never
loads the dataset. The inventory checker validates its index
and exact allowed paths independently of runtime manifests and artifact assembly;
the separate runner checks per-turn contracts. Questions
and Retrieval use narrow Git allowlists; Retrieval retains its existing generated
pair/source declarations. See [Git policy](docs/runtime-artifacts.md#git-policy-for-questions-and-retrieval)
and [question maintenance](artifacts/questions/README.md).

## Implemented storage and ingestion

The schema chain is the applied `bce4184a7f51` baseline, additive `c7d92e6a104b` publication guards, then `d8e031c72b19` conversation storage (current head). There are 17 application tables: 14 corpus/publication tables and 3 conversation tables. The guards preserve tables/data and restore timestamp, immutable revision identity and append-only audit triggers. Alembic executes pending revisions in dependency order; fresh baseline installation requires vector provisioning before the baseline, not at the later guard revision. Follow the [database provisioning procedure](src/database/README.md#fresh-database-provisioning); older migration histories need separate reconciliation. See [database design](docs/database-design.md) for the corpus data dictionary, invariant ownership, execution receipts and metadata audit, and [conversation storage](docs/conversations.md#enablement-and-storage) for chat provisioning.

Python 3.14, async SQLAlchemy/psycopg and PostgreSQL/pgvector provide Type A catalog/vector/structured evidence and separate conversation tables with explicit migrations. Engines remain lazy; entrypoint imports do not connect to the database. E5-small retrieval and source-bound templates are implemented. Type B uses its independent readonly connection. Opt-in conversations persist history and structured context; broader operational tools remain future work. Synthetic tests do not establish real-model quality.

Source documents stay in `assets/`. `src.paths` anchors Git-trackable current packages at `chunks/<document_id>/`. Metadata is the sole owner of source identity, including revision_id; the directory name is document_id. Source-path uniqueness is checked case-insensitively. Git stores committed package history, while each checkout has only the current package. Per-document OS locks protect reads against replacement; staging, recovery markers and displaced packages live under ignored `.tmp/`. An interrupted replacement blocks reads until explicit recovery. Locks coordinate Python commands, not external editors; stop editing while validation/publication runs. Each complete package contains three consistently versioned v2 or v3 JSON files (metadata, extraction and chunks) and unchanged source bytes. Initialization may leave an incomplete package until the agent authors extraction/chunks. Historical `indexing_chunks/` is never scanned or merged.

Agent-authored transcription and semantic chunking are the primary workflow; automatic Python loaders/chunkers remain retired. Package creation and validation are database/model-free. Agents preserve raw transcription, source locations, character spans, explicit exclusions and warning decisions. Image-recognized text is marked `ocr`, with the actual method recorded rather than an inferred engine identity. Structural validation cannot establish visual fidelity, transcription completeness or business correctness. There is no approval state or human approval command.

Publication reads a package into memory, validates eligibility, fingerprints all input bytes and embeds exactly that snapshot using an explicitly installed local model. It rechecks the package before writing and under the database publication lock. Metadata, access rules, revision relations, chunks/vectors and active-run changes share one transaction. Existing metadata changes need `--update-metadata`. For an existing revision, document ID, source hash, size and extension are immutable; the runtime currently allows source-path metadata changes with that flag when no collision exists. This does not rename the source file or replace the agent authoring rule to preserve identity and create a new revision for changed bytes. Explicit supersedes relations control revision replacement. Local revise requires `--supersedes-current` and keeps the declared ancestor list, so an environment can upgrade across skipped source revisions. Missing supersedes targets are recorded in run configuration and reported, without creating placeholder database rows; other missing relation targets still reject publication. There is no catalog-wide retirement or rechunking.

An independent, database-free technical preflight loads structurally valid packages without requiring publication eligibility. It shares local model loading, input preparation, token counting, encoding, vector validation and model fingerprinting with publication through `src.ingestion.embeddings`. By default it loads the model and checks tokens only; optional full-package embedding checks discard vectors after validation. Reports contain counts, fingerprints and separate metadata blockers, never source text or vectors. Successful checks recheck package/model bytes, create no durable approval state and do not bypass publication checks. Database conflicts and relation targets are outside preflight. Model commits remain declarations, not authentication of the local model files.

Run configuration retains source/content/metadata/package fingerprints and model configuration, without new approval fields. Publication comparison and exact-text vector reuse live in `src.ingestion.publication_state`. Canonical runtime content/provenance plus verified stored chunks/evidence determine skips; byte hashes protect snapshots and preserve original provenance. Explicit metadata-only updates keep run/chunk rows unchanged. Changed publication creates a full new run and reuses compatible vectors from the active same-revision run or current explicitly superseded same-document revision. No cross-document cache is introduced; the current schema requires explicit baseline/guard provisioning. `--force` still creates a run and encodes all chunks. Old successful runs retain their original JSON configuration; new runs declare fingerprint/preprocessing versions. Storage and source-run tokens are rechecked under the publication lock. See [publication decisions and compatibility](docs/offline-indexing.md#scope-2-package-reading-embedding-and-publication) and the [corpus publication runbook](docs/corpus-publication.md). Validated real executions have a separately committed attempt before embedding. Known failures preserve the previous active run and finalize the attempt; uncertain commits retain an unresolved attempt ID for receipt reconciliation, without automatic retry. Local models are provisioned before runtime, including the selected Git/LFS inventory, without runtime downloads or managed caches. Dry-run checks packages, model fingerprints and current database state but does not tokenize or embed chunks.

Metadata requires include/active state, applicability, access groups and valid effective dates for publication; owner and evidence notes are optional. Access metadata does not implement future backend authorization. The baseline uses evidence notes without review status. The retired migration chain is not a supported in-place conversion path.

Existing review/approved packages are excluded from normal scans. An explicit preview-first converter validates the selected legacy package and writes the new layout without modifying or deleting originals. Conversion also applies stricter v2 extraction/chunk validation, so some valid legacy locations (for example XLSX without a sheet name) are rejected rather than inferred or silently repaired. Historical destructive workspace migration is retired. See the [package contract](docs/offline-indexing.md) for conversion, schemas and rollout.

## Offline authoring boundaries

Scope 1 ends at the complete, source-checked and structurally validated package. Scope 2 consumes it for actual tokenizer/model preflight, embedding and transactional publication. Model checks are not required to author a package, and source fidelity remains an authoring responsibility even when technical checks pass. See the [authoring procedure](docs/offline-indexing.md#agent-authoring-procedure).

Within schema v2, authors can compose detail, inventory and source-defined group chunks using the existing content and source references. These levels are conventions in text, not runtime types. Detail chunks retain necessary eligibility and exceptions while avoiding unrelated repeated material. Inventories have explicit source boundaries and preserve main-item/subitem labels; neither chunk count nor top-K matches establishes a complete benefit count.

V3 explicitly represents evidence roles, stable author IDs, item hierarchy and authored coverage; v2 retains its text-only conventions. See [structured evidence](docs/structured-evidence.md). Optional notes are not embedded, and revision relations do not connect chunks. Structural checks do not replace semantic source review or real retrieval evaluation.

## Offline publication boundaries

[Scope 2](docs/offline-indexing.md#scope-2-package-reading-embedding-and-publication) reads validated v2/v3 packages, performs local-model checks and publishes atomically per revision. Preflight is independent and leaves no reusable vectors or passed-state prerequisite; database-aware dry-run does not tokenize or encode. Publication embeds the complete chunk content with `passage: `, stores that content without the prefix, and preserves resolved source spans. It does not derive runtime chunk types or hierarchy from authoring conventions.

The encoder requests normalized 384-dimensional vectors; validation checks count, indices, dimensions and finite values, not independent unit norm or semantic accuracy. Publication fingerprints the package/model, checks concurrent changes and verifies stored chunks/evidence before skipping an unchanged publication. Missing text is encoded with temporary contiguous indices and then assigned to the complete destination snapshot; moved chunks retain their new indices/provenance. Current revision, active run, policy eligibility and retrieval model compatibility remain distinct conditions. Retrieval continues filtering active successful runs and current eligible model-compatible revisions; historical chunk rows are not searched. V3 adds run-owned structured item/coverage storage. Separate embedding text remains future work.

## Service transport

HTTP through FastAPI is the only implemented service transport. The backend calls
`POST /api/v1/retrieve`; the separately gated `POST /api/v1/answer` remains opt-in.
Python accepts only a `127.0.0.1` bind. A reverse proxy on the bot's network
namespace can expose HTTPS to Backend on another domain or network. Backend
accepts remote HTTPS origins and literal loopback HTTP, with fixed timeout/top-K.
Every /api/v1 operation and both readiness probes require CHAT_BOT_SERVICE_KEY;
liveness stays public. A pure ASGI boundary preserves receive/send and existing
disconnect/drain behavior. Type B/conversation no longer own credentials or
HTTP bypass flags. See [shared HTTP authentication](docs/http-service-auth.md).

The former health-only gRPC server and entrypoint have been removed, without a
compatibility stub. Use `/health/live` for HTTP liveness and `/health/ready` for
initialized retrieval/model state and database connectivity. Readiness does not
certify answer-catalog availability, retrieval quality or corpus completeness.
See [HTTP operation and rollout](README.md#http-service-operation-and-rollout).

## Leave recognition and source-bound ordering

`src/retrieval/policy.py` loads the optional source-bound retrieval catalog independently of answer enablement. Lexicon concept IDs drive bounded family/type/platform/operation recognition and day/session extraction; recognition-only concepts never participate in E5 phrase expansion. An identified leave request stably reorders the existing eligible pool before top-K using validated source bindings, role/platform and authored ranges. Unknown/stale metadata is neutral; explicit other families follow matching candidates. Explicit non-leave queries preserve baseline order. Raw logits are unchanged and relevance remains unassessed.

`src/answering/pipeline.py` consumes the same policy for targeted missing-slot clarification and task selection. Known narrow legacy tasks retain their contracts, supported specified requests read exact current DB references, and unsupported branches do not substitute another system. No balances or personal eligibility are inferred. The storage adapter supplies source hashes internally and retrieval removes them before all public serialization. HTTP/CLI schemas are unchanged.

`src/artifact_authoring` now builds/imports two or three groups. The retrieval source index declares families, systems, subtypes, operations, bindings and routes; build binds its lexicon hash, impact/refresh checks ranking and answer references together. Missing/invalid optional catalogs degrade to baseline retrieval without a new env switch. See [contract, benchmark and limits](docs/leave-retrieval.md). This task does not modify conversation storage or cache lifecycles.

## Implemented public-document retrieval

`src.retrieval.service` owns local E5 encoding, optional ONNX reranking and guarded semantic alternatives. Candidate retrieval shares readonly REPEATABLE READ snapshots, filters eligible current public documents and compatible active successful runs, then returns at most top-K unassessed candidates. Effective dates use Asia/Ho_Chi_Minh. Distances/logits are not confidence or answer authorization.

`src.retrieval.storage.read_references` reads authored evidence addresses independently of top-K in one readonly snapshot, applying the same eligibility/model/access conditions. The renderer validates source/content hashes, exact quotes and metadata. Source IDs, spans and revision/run provenance remain database-owned. There are no intent heads, relevance profiles or learned scoring paths.

Model work remains serialized off the event loop; cancellation drains workers, HTTP owns the busy slot, and each request owns its DB session. No model downloads or import-time DB connections occur. See [runtime contract](docs/catalog-answering.md#runtime-contract).

## Structured evidence (schema v3)

V2 remains supported. V3 adds stable author chunk IDs/roles and source-backed scopes, items and typed values in the same four-file package. The fresh baseline retains nullable v2 chunk fields and adds run-owned evidence tables with same-run normalized scope/item/chunk links and item-parent FKs. Structure participates in publication fingerprints and commits atomically with vectors; shared readers verify projections against authored JSON before reuse or expansion. Historical runs remain intact. A selected active run is the consistency boundary; every expansion rechecks public/effective/current/model-compatible eligibility.

Structured scopes, items and values remain supported by package validation, publication and storage. The retrieval API uses vector candidate search with optional local CPU cross-encoder reranking; it does not select scopes, expand item membership or compute aggregate answers. Retained storage helpers and their tests protect data compatibility. See [the contract and limits](docs/structured-evidence.md).

## Local retrieval HTTP API

`src.api` owns HTTP configuration, typed request/response contracts and application
lifecycle. `POST /api/v1/retrieve` requires the shared service key and a trusted
Backend-derived `hrm_user_id`. It uses `RetrievalService.retrieve()` for unassessed
source matches, reports retrieval and total timings, and can include bounded HRM
evidence when configured. Optional `top_k` overrides `RETRIEVAL_TOP_K`. Strict
validation rejects extra fields and invalid types. The former `/api/v1/ask`
route has been retired.

HTTP configuration loading in `src/api/config.py` applies repository `.env`
before process environment, then defaults for absent keys. All loaded keys,
including downstream database URLs, follow this HTTP startup policy; standalone
database/CLI loaders are unchanged. No configuration is hot-reloaded. Logs expose
the effective source and value of `ANSWER_ENABLED` and `SEMANTIC_MODE`, fixed
conflict warnings, and the names of ignored retired keys.

`AnswerService` owns answer startup state/reason and clears its catalog/lexicon
on failure or shutdown. `/health/answer` checks retrieval/database readiness and
this answer state without running a question or acquiring the shared busy slot.
It returns typed ready/unavailable metadata with HTTP 200/503; dependency failures
take precedence. Existing `/health/live`, `/health/ready` and answer errors remain
compatible. Fixed reason codes, safe processing logs and operator actions are
documented in [answer diagnostics](docs/answer-evaluation.md#configuration-and-validation).

Startup loads and fingerprints local E5-small once on CPU, checks PostgreSQL, and only then serves requests. Imports do not initialize models or connections. No downloads, migrations or publication occur. A single worker uses the Windows-compatible async runner. Embedding runs off the event loop; one HTTP retrieval owns the busy state through database search and response validation. Additional requests receive 429. A 120-second deadline requests cancellation, but cancellation (including repeated cancellation) drains embedding work before releasing ownership. Shutdown waits for active requests, releases E5 and closes the database, including partial-startup cleanup.

Readiness checks model initialization and database availability, not semantic quality or dataset completeness. Errors distinguish invalid requests (422), busy (429), unavailable dependencies/model mismatch (503) and timeout (504). Valid empty results remain 200. Logs contain fixed status/error codes and timings, not questions or source content. The retrieval route has no LLM planner, generator, verifier, confidence mapping or automatic aggregate computation. Top-K results cannot certify completeness or answerability. See [manual retrieval evaluation](docs/retrieval-evaluation.md).

## Planned operational retrieval and extended answering

Current deterministic policy answering lives in `src.answering`. The selected
schema-3 answer catalog has stable task and evidence IDs, scoped aliases and
concepts, clarification prompts, aspect sections, exact references and contextual
requirements. The schema-2 lexical artifact is hash-paired with the catalog.
Snapshot fingerprints record authoring provenance. Schema-1 runtime pairs are
rejected.

An unambiguous task reads its declared evidence directly from current eligible DB publications, independently of top-K. Claims require every reference; partial evidence can yield independently supported partial claims. Unknown tasks expose no nearest excerpts. Explicit document lookups can return labeled unassessed excerpts. Multi-task, unsupported personal eligibility, aggregate and ambiguous questions ask for clarification. This changes the old top-K-only evidence policy intentionally.

Type B uses the bounded self-reference registry and readonly employee adapter. Unified evidence preserves policy/fact provenance without deciding eligibility. Structured conversation memory is independently opt-in; broader tools, cross-employee lookup, automatic aggregates and generative answers remain future work.

`src.artifact_authoring.impact` reports dependency-local drift and relinks only unique byte-identical evidence with unchanged revision/source/context. Build/check generates compatible artifacts. Content/revision changes require targeted review, not fingerprint edits or training. See [authoring/release](docs/catalog-answering.md).

## Trust, freshness, and failure behavior

- Preserve document identity, versions, effective dates, applicability, and replacement/supplement relationships. A newer filename alone does not establish policy precedence.
- Apply access restrictions before source content reaches the model. Tool authorization belongs in the backend, not in model instructions alone.
- Resolve “me” from authenticated identity. A typed employee identifier does not grant access to that employee's information.
- Treat retrieved documents and tool outputs as evidence, not instructions that can override application behavior.
- Distinguish no matching evidence, disconnected sources, upstream errors, and access denial. Do not replace a failed Type B query with an answer based on stale documents.
- When sources conflict, explain the unresolved issue or ask for clarification instead of silently selecting a result. Absence of a retrieved policy is not proof that no policy exists.
- Keep citations tied to retrieved evidence. Conversation history supports interpretation but cannot establish business facts on its own.

## Delivery phases and evaluation

Source update/reconciliation is owned by `src.artifact_authoring.impact`; deterministic build/check remains in `src.artifact_authoring.workflow`. General retrieval and template outcome evaluators remain separate from business acceptance. Calibration, learned scoring and intent training are retired.

| Phase | Deliverable | Validation focus |
| --- | --- | --- |
| 0 — implemented | Database foundation, explicit migrations, package-owned Type A metadata, agent-authored JSON batch ingestion, atomic validated-package publication and loopback HTTP transport | Database behavior; source identity, consistency, history and metadata validation; HTTP health and lifecycle |
| 1 — implemented, acceptance pending | Public-document retrieval, source-bound template answers, offline evaluation and real-model diagnostics | Correct evidence, extraction quality, obsolete-document handling and independent catalog quality acceptance |
| 2 — limited, opt-in | Service-authenticated work-profile evidence for the authenticated employee and bounded manager summaries. Backend owns user authorization | Readonly source contracts, field selection, unavailable data and user rollout |
| 3 — limited, opt-in | Supported policy and HRM evidence in one answer, with independently opt-in durable structured follow-ups | Context recovery, fresh source reads, conflicting or incomplete evidence and consumer compatibility |

Broader employee lookup, project tools, personal entitlement decisions and
general-purpose rule-to-person automation remain outside the implemented scope.

Use representative Vietnamese questions, paraphrases, follow-ups, ambiguous
requests and unanswerable questions. Measure retrieval quality and answer grounding
separately, alongside latency and resource use. Live employee facts require the
configured readonly source and authorized identity.

FastAPI, local E5/ONNX runtimes and source-bound templates are implemented; their existence does not establish production acceptance. Authenticated conversation integration is opt-in; production sizing and live rollout acceptance remain outstanding. The current 17-table schema (14 source/evidence tables plus three chat tables) and 384-dimensional embeddings establish storage only. Fine-tuning should follow a measured need rather than be an initial prerequisite.

## Verification boundaries

See [current verification](README.md#current-verification-status) for the latest checked working tree and test limits. The retrieval evaluator has a [known reporting gap](docs/retrieval-evaluation.md#known-evaluator-reporting-gap); its legacy candidate fields do not establish candidate recall.

Answer HTTP lifecycle regressions drive the actual ASGI route with fictional
retrieval workers and explicit disconnect events. They check timeout/disconnect
draining, watcher cleanup, shared busy ownership and lifespan shutdown, plus
catalog-failure isolation from retrieval/health. This complements service-level
repeated-cancellation tests; it does not establish proxy/network behavior or
real-model cancellation latency. HTTP and CLI regressions cover unassessed
candidates with retired relevance-filter settings ignored.

The [2026-11-13 offline corpus preparation](docs/chunk-coverage-verification.md) adds source-authored policy and software-procedure evidence without changing runtime composition. Its in-memory CPU diagnostic includes held packages and is deliberately separate from eligible-publication retrieval and passage-relevance calibration. It does not establish live API quality or current personnel facts.

Unit tests cover synthetic package contracts, converter compatibility, CLI batch ordering and publication boundaries; HTTP tests exercise retrieval contracts, health and lifecycle. They cannot establish transcription accuracy or real embedding-model behavior. PostgreSQL integration tests use guarded disposable databases and synthetic embeddings to exercise publication, revision relations, transaction rollback and migration preservation. They perform schema changes and application-table writes/cleanup, including downgrade/upgrade; see [test setup and safety](README.md#testing). Missing configuration means skipped verification, not a successful database check. Historical rename-utility tests remain compatibility checks outside the v2 ingestion workflow.

## Optional online reranking

Pinned local CPU ONNX reranking sorts unassessed candidates. It has no profile/threshold/learned scoring gate. Preserve model hash validation, long-passage windows and failure/cancellation handling. See [configuration](docs/online-reranking.md).

## Optional intent observation

Retired. Deterministic catalog concepts and the Type B task registry replace classifier observation/assistance/source hints. No extra classifier artifact is loaded.

## Relevance filtering and empty fallback

Retrieval now returns unassessed candidates, with explicit warnings; no profile-based filtering occurs. Empty outcomes distinguish no candidates/no eligible documents from operational failures. Answer eligibility is checked against exact source evidence per task. See [catalog answering](docs/catalog-answering.md).
