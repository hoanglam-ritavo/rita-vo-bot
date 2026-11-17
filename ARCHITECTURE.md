# Rita Bot Architecture

## Readonly HRM source foundation

`src.employee_source` owns a lazy independent PostgreSQL/psycopg connection,
fixed 64-column employee_sso_store repository, immutable validated contracts and
service reads by one/batched user_id. HRM_DATABASE_URL never falls back to the
Type A database; schema defaults to public. All source relations are readonly:
dedicated externally provisioned SELECT-only privileges, readonly connection
defaults and REPEATABLE READ / READ ONLY transactions. No ORM/Alembic registration,
source writes, preload, cache, alias/account access or synchronization is added.
The backend's microsecond timestamp/completeness/employee_id selector is ported,
including inactive winners and conflict failures before normalization.

This is an internal data-access foundation, not a connected Type B tool: no API,
intent routing, model context, authentication or authorization integration uses
it. The opt-in check_employee_source CLI checks metadata/readonly capabilities
without returning personnel. See [employee source](docs/employee-source.md) for
the full property dictionary, lifecycle, role prerequisites, semantic unknowns,
errors and preprovisioned disposable test contract.

## Command ownership

[Application entrypoints](src/cli/README.md) remain in `src/cli/`; [manual utilities, offline evaluations and experiments](scripts/README.md) remain in `scripts/`. Both inventories distinguish current commands, compatibility aliases, retired stubs and support modules. Existing command paths remain supported. One-shot retrieval lifecycle/timings live in `src.retrieval.runner`, used by the CLI compatibility wrapper and IDE utility; HTTP continues to own its long-lived service lifecycle. Pure model revision validation lives in `src.ingestion.model_identity`, shared by publication, preflight and evaluation without imports between those CLI implementations. Historical asset tools retain their old `app/` paths and are not current setup commands.

## Implemented storage and ingestion

The schema chain is the applied `bce4184a7f51` baseline followed by additive `c7d92e6a104b` publication guards. The latter preserves tables/data and restores timestamp, immutable revision identity and append-only audit triggers. Fresh baseline installation requires vector provisioning first; older migration histories need separate reconciliation. See [database design](docs/database-design.md) for the 14-table data dictionary, invariant ownership, execution receipts, metadata audit and initialization boundaries.

Python 3.14, async SQLAlchemy/psycopg and PostgreSQL/pgvector provide Type A catalog, vector and structured-evidence tables with explicit Alembic migrations. Engines remain lazy; entrypoint imports do not connect to the database. Public-document retrieval function/CLI and local HTTP source retrieval are implemented. E5-small runs directly in Python; source-bound template answering has a separate disabled-by-default catalog-gated API; authenticated Type B integration and conversation history remain planned. Real-model retrieval quality is not established by synthetic tests.

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
The API and backend must share the same machine/network namespace: the bot accepts
only a `127.0.0.1` bind, and the backend accepts only a loopback HTTP origin.
There is no service authentication or remote/container-network support.

The former health-only gRPC server and entrypoint have been removed, without a
compatibility stub. Use `/health/live` for HTTP liveness and `/health/ready` for
initialized retrieval/model state and database connectivity. Readiness does not
certify answer-catalog availability, retrieval quality or corpus completeness.
See [HTTP operation and rollout](README.md#http-service-operation-and-rollout).

## Implemented public-document retrieval

The experimental local learned-scoring path is owned by
`src/retrieval/scoring.py` and `scoring_training.py`. It keeps the pinned
E5/ONNX models and 30 candidates, derives eight fixed passage features, and
binds fitted coefficients into the runtime fingerprint. Schema-v3 profiles
carry coefficients; schema-v2 retains raw-max scoring. Development-only fitting
and paired collection cannot issue acceptance. Public results omit numeric
feature diagnostics. See [scoring and acceptance](docs/relevance-scoring.md).

Optional corpus-bound Vietnamese expansion is owned by `src.retrieval.semantics`
and offline preparation by `src.semantics`. It defaults off, preserves source
content and uses a separately reviewed language artifact rather than document-ID
ranking rules. Semantic queries share eligibility filters and the existing
30-candidate budget; the rerank query remains the normalized/typo-corrected
original. Effective pipeline fingerprints bind the language artifact and any
optional intent-assistance identity. Source and runtime acceptance remain
separate. See [Vietnamese semantics](docs/vietnamese-semantics.md) for coverage,
review, evaluation and rollout contracts. No package schema, stored vector,
database migration or public retrieval wire change is involved.

`src.retrieval.RetrievalService` accepts an independent question string and returns nearest-neighbor chunks with source provenance. Both the retrieval CLI and HTTP retrieval service reuse it; no chat RPC is implemented. The service loads and fingerprints a local embedding model once, embeds questions with `query: `, checks token limits without truncation and validates/normalizes the 384-dimensional query vector. Model work is serialized in worker threads; each database request owns a separate read-only repeatable-read session. The application closes the shared database at shutdown, independently of service close. Runtime model files are immutable for the service lifetime.

Exact cosine retrieval filters current include/active revisions, inclusive effective dates using the shared Asia/Ho_Chi_Minh business date, as publication does, public access rules and active successful runs before ranking. Model name, revision, dimensions, file fingerprint, normalization and indexed passage prefix must match. Public access uses EXISTS to avoid duplicate matches; ties use chunk IDs. Results include source spans and document metadata, never vectors. Empty eligibility, model incompatibility, absent vectors and operational failures remain distinct. Cosine search supplies candidates internally; public results require a matching per-passage relevance profile by default and may be empty. HTTP configuration can explicitly disable this gate to return unassessed top-k candidates; access and eligibility filters still apply. Accepted excerpts do not establish sufficient evidence, truth or answerability. Private-document authentication and conversation resolution remain future work. The retrieval HTTP route below exposes the same source retrieval without generation; the separately gated answer route consumes accepted excerpts.

Synthetic unit tests cover query input, vector checks, model lifecycle and safe errors. Dedicated disposable PostgreSQL integration tests cover ranking and eligibility filters; they must not use the application database. Real-model retrieval evaluation is a separate read-only exercise against an explicitly selected dataset, measuring expected-source recall and latency without updating packages or database rows.

## Structured evidence (schema v3)

V2 remains supported. V3 adds stable author chunk IDs/roles and source-backed scopes, items and typed values in the same four-file package. The fresh baseline retains nullable v2 chunk fields and adds run-owned evidence tables with same-run normalized scope/item/chunk links and item-parent FKs. Structure participates in publication fingerprints and commits atomically with vectors; shared readers verify projections against authored JSON before reuse or expansion. Historical runs remain intact. A selected active run is the consistency boundary; every expansion rechecks public/effective/current/model-compatible eligibility.

Structured scopes, items and values remain supported by package validation, publication and storage. The retrieval API uses vector candidate search with optional local CPU cross-encoder reranking; it does not select scopes, expand item membership or compute aggregate answers. Retained storage helpers and their tests protect data compatibility. See [the contract and limits](docs/structured-evidence.md).

## Local retrieval HTTP API

`src.api` owns HTTP configuration, typed request/response contracts and application lifecycle. `POST /api/v1/retrieve` reuses `RetrievalService.retrieve()` and returns source matches plus retrieval/total timings. Optional request `top_k` overrides `RETRIEVAL_TOP_K`; strict validation rejects extra fields and invalid types. The former `/api/v1/ask` route is removed without an alias.

Startup loads and fingerprints local E5-small once on CPU, checks PostgreSQL, and only then serves requests. Imports do not initialize models or connections. No downloads, migrations or publication occur. A single worker uses the Windows-compatible async runner. Embedding runs off the event loop; one HTTP retrieval owns the busy state through database search and response validation. Additional requests receive 429. A 120-second deadline requests cancellation, but cancellation (including repeated cancellation) drains embedding work before releasing ownership. Shutdown waits for active requests, releases E5 and closes the database, including partial-startup cleanup.

Readiness checks model initialization and database availability, not semantic quality or dataset completeness. Errors distinguish invalid requests (422), busy (429), unavailable dependencies/model mismatch (503) and timeout (504). Valid empty results remain 200. Logs contain fixed status/error codes and timings, not questions or source content. The retrieval route has no LLM planner, generator, verifier, confidence mapping or automatic aggregate computation. Top-K results cannot certify completeness or answerability. See [manual retrieval evaluation](docs/retrieval-evaluation.md).

## Planned operational retrieval and extended answering

`src.answering` owns the source-bound catalog, strict claims/citations and
deterministic renderer. There is no generative model, GGUF loader, model verifier
or automatic repair. `src.api.answer` retains the shared busy slot and a 120-second
request deadline, including retrieval. Cancellation drains retrieval workers;
template processing has no uncertain native generation context to disable.

Startup validates catalog/package/lexicon fingerprints and all source references.
Bad or missing catalog disables answering while retrieval remains available.
The semantic phrase matcher is shared without changing retrieval expansion/ranking.
At request time, a unique catalog concept/alias and question aspect select a
template. Every required source must occur in the current returned matches with
matching document/revision/index/content/metadata. No extra evidence fetch or
DB-ID routing occurs. Response-only origins stay application-owned.

Catalog coverage inventories every chunk; selected units retain source wording
or authored prose with exact quotes and conditions. Complete coverage is local
to the declared aspect, never inferred from top-K. Ambiguity and unsupported
personal/aggregate questions ask for clarification. Missing templates/evidence
return labeled verbatim excerpts, or insufficient evidence when none exist.
Filtered/unassessed retrieval policy is preserved without a model-verifier gate.
The API request/response shape stays compatible; generation/verification seconds
are zero, and template/rendering overhead is included in total seconds. Logs
contain generated request IDs, status and timings, not questions or raw content.
See [current contract](docs/answer-evaluation.md#semantic-template-answer-api).
`scripts.evaluate_answers` is a retired stub; historical studies cannot authorize
the new template runtime. Models and source/database packages are untouched.

Type A documents provide policy evidence; Type B tools provide authoritative current operational facts. Preserve applicability, access restrictions, source citations and explicit unavailable/conflicting-source handling. Exact lookup/computation is needed for structured questions; the stored chunks and vectors alone are not a table query engine. Models are locally operated; internal data must not be uploaded as an implicit implementation choice.

### Type B: current operational evidence

Resolve the authenticated user and required entity identifiers, validate parameters and permissions in the backend, and call a defined read-only function. Initial examples are retrieving a person's direct manager, active project memberships, or a project's responsible person. Future service integration will use HTTP APIs; the Type B contract and authentication mechanism are not yet defined or implemented.

The model proposes a function and arguments; the backend controls execution. Arbitrary model-generated SQL is not the initial approach. A copied embedding index must not become the source of truth for current reporting relationships or assignments. Responses should identify the operational source and preserve relevant freshness information supplied by that source.

### Routing and combined questions

Use model interpretation together with deterministic backend checks. Supply descriptions of each source/tool's capabilities and required parameters. Consider conversation history and required evidence rather than keywords alone. Model-reported confidence is not sufficient proof that a route or answer is correct.

| Example | Intended behavior |
| --- | --- |
| “What is the leave application procedure?” | Retrieve Type A policy and cite it |
| “Who is my direct manager?” | Resolve authenticated identity and query Type B |
| “Under the procedure, whom should I send this form to?” | Find the approval role in A, then resolve the current person through B |
| “What about unpaid leave?” | Resolve the previous topic, then retrieve the relevant policy |
| “Who manages that project?” with no identifiable project | Ask for clarification before querying |
| “Hello” | Reply directly without business-data retrieval |
| A Type B request before integration exists | Explain that the operational source is not connected |

Combined retrieval may be sequential: first determine the approval rule, then query the person holding that role. Do not assume the direct manager is the approver unless the policy supports that relationship. No multi-agent architecture is required. After retrieval, check that evidence covers the question; otherwise clarify or state the limitation.

## Trust, freshness, and failure behavior

- Preserve document identity, versions, effective dates, applicability, and replacement/supplement relationships. A newer filename alone does not establish policy precedence.
- Apply access restrictions before source content reaches the model. Tool authorization belongs in the backend, not in model instructions alone.
- Resolve “me” from authenticated identity. A typed employee identifier does not grant access to that employee's information.
- Treat retrieved documents and tool outputs as evidence, not instructions that can override application behavior.
- Distinguish no matching evidence, disconnected sources, upstream errors, and access denial. Do not replace a failed Type B query with an answer based on stale documents.
- When sources conflict, explain the unresolved issue or ask for clarification instead of silently selecting a result. Absence of a retrieved policy is not proof that no policy exists.
- Keep citations tied to retrieved evidence. Conversation history supports interpretation but cannot establish business facts on its own.

## Delivery phases and evaluation

Baseline calibration preparation is owned by `src.cli.prepare_rerank`, with
model-free workspace import/validation/freeze in `src.retrieval.rerank_preparation`
and shared evaluator case rules in `src.retrieval.evaluation_cases`. Preparation
uses existing package validation and canonical identities without database or
model access. The separate baseline benchmark records authored-input/package
bindings and reproduction settings; freeze permits candidate-label edits only
and checks operator review declarations before snapshotting. It neither certifies
semantic review nor changes calibration gates or runtime APIs. See
[baseline preparation](docs/online-reranking.md#prepare-baseline-calibration-inputs).

| Phase | Deliverable | Validation focus |
| --- | --- | --- |
| 0 — implemented | Database foundation, explicit migrations, package-owned Type A metadata, agent-authored JSON batch ingestion, atomic validated-package publication and loopback HTTP transport | Database behavior; source identity, consistency, history and metadata validation; HTTP health and lifecycle |
| 1 — partial | Public retrieval function/CLI/HTTP, offline evaluation tools and real-model diagnostics implemented; corpus-matched calibration acceptance, integrated generated answers and follow-up handling remain outstanding | Correct evidence, extraction quality, exact table answers, obsolete-document handling, unsupported B requests |
| 2 — planned | Authenticated, authorized Type B read-only tools | Identity/entity resolution, permission enforcement, freshness, unavailable services |
| 3 — planned | Combined A/B questions | Rule-to-person resolution, ordering of dependent calls, conflicting or incomplete evidence |

Use representative Vietnamese questions, paraphrases, follow-ups, ambiguous requests, and unanswerable questions. Measure retrieval quality and answer grounding separately, alongside latency and resource use. Phase 1 must not claim live personnel/project knowledge before Phase 2 exists.

FastAPI, local E5/ONNX runtimes, ranking configuration and quantitative offline gates are implemented; their existence does not establish production acceptance. Production sizing, authenticated HTTP service integration and extended answering remain future work. The current 14-table application schema, including run-owned structured evidence, and 384-dimensional embeddings establish storage only. Fine-tuning should follow a measured need rather than be an initial prerequisite.

## Verification boundaries

See [current verification](README.md#current-verification-status) for the latest checked working tree and test limits. The public filtered evaluator has a [known reporting gap](docs/retrieval-evaluation.md#known-evaluator-reporting-gap); its legacy candidate fields do not establish candidate recall.

Answer HTTP lifecycle regressions drive the actual ASGI route with fictional
retrieval workers and explicit disconnect events. They check timeout/disconnect
draining, watcher cleanup, shared busy ownership and lifespan shutdown, plus
catalog-failure isolation from retrieval/health. This complements service-level
repeated-cancellation tests; it does not establish proxy/network behavior or
real-model cancellation latency. CLI coverage retains filtering even when the
HTTP-only environment setting disables it.

The [2026-11-13 offline corpus preparation](docs/chunk-coverage-verification.md) adds source-authored policy and software-procedure evidence without changing runtime composition. Its in-memory CPU diagnostic includes held packages and is deliberately separate from eligible-publication retrieval and passage-relevance calibration. It does not establish live API quality or current personnel facts.

Unit tests cover synthetic package contracts, converter compatibility, CLI batch ordering and publication boundaries; HTTP tests exercise retrieval contracts, health and lifecycle. They cannot establish transcription accuracy or real embedding-model behavior. PostgreSQL integration tests use guarded disposable databases and synthetic embeddings to exercise publication, revision relations, transaction rollback and migration preservation. They perform schema changes and application-table writes/cleanup, including downgrade/upgrade; see [test setup and safety](README.md#testing). Missing configuration means skipped verification, not a successful database check. Historical rename-utility tests remain compatibility checks outside the v2 ingestion workflow.

## Optional online reranking

Runtime E5 defaults to `models/embeddings/multilingual-e5-small/<revision>/`; the existing pinned ONNX/tokenizer layout stays directly under `models/`. The model allowlist uses Git LFS for weights, SentencePiece binaries and large tokenizer JSON, and byte-preserving Git attributes for the entire inventory. Versioned accepted profile/lexicon/classifier JSON and their provenance manifests can be tracked under `artifacts/`; local verification receipts and private `.tmp/` evaluation workspaces remain ignored. `artifacts/runtime-assets.json` records model identities and file hashes. The standard-library-only `scripts.verify_runtime_assets` validates local bytes, hydrated LFS files, the complete E5 tree and Git-tracked asset paths; it neither loads models nor validates live corpus compatibility. Backup/restore and calibration promotion remain separate operational gates described in [runtime artifacts](docs/runtime-artifacts.md).

Semantic expansion additionally reads every package JSON and source snapshot in `chunks/` at startup to check its package fingerprint, including packages not active in PostgreSQL. Relevance profiles bind the eligible database corpus using publication run IDs, revision IDs and configurations; independently republishing identical packages generates new run IDs and invalidates profile reuse. Shipping model/artifact files alone does not provision or certify that database state. No startup download, generation, schema, response or health-contract change is introduced by Git/LFS storage.

`src.retrieval` owns the pinned mMARCO MiniLM ONNX lifecycle and shared function/CLI/HTTP pipeline. Enabled requests normalize NFC/whitespace, retrieve original plus an optional allowlisted typo correction in one read-only snapshot, fuse candidates with RRF, and rerank overlapping token windows on CPU. Content and provenance are preserved; candidate selection/order, per-passage filtering and explicit empty fallback are shared across public entrypoints. The model is loaded locally with pinned artifact hashes; no ingestion, schema or vector writes occur. Runtime failure does not silently fall back. The service lock spans retrieval and reranking so close/cancellation drains work before releasing models. See [online reranking](docs/online-reranking.md) for configuration, limits, profile calibration and rollout gates.


## Optional intent observation

`src.intent` owns a local JSON linear classifier over the already-created original E5 query vector. One multiclass head predicts A/B/mixed/conversation/out-of-scope/missing-context; five binary heads identify Type A evidence needs. `RetrievalService` owns its lifecycle under the existing worker/lock boundary. HTTP settings and the retrieval CLI opt in explicitly; the default is off. In `observe` mode, the observer logs only labels/status/artifact identity/timing and never enters result serialization, alters query processing, filters candidates, changes ranking, bypasses relevance assessment, or calls a tool. Invalid or unaccepted artifacts make the observer unavailable without suppressing retrieval errors. No additional encoder, model server, schema or response field is introduced.

The separate `assist` mode requires semantic expansion and uses accepted style
hints to prioritize already-valid alternatives; source labels never exclude
documents or invoke tools. Uncertain/unavailable inference retains unassisted
alternatives, while the effective fingerprint still identifies the configured
assistance pipeline and loaded classifier version. A missing classifier at
startup therefore does not silently reuse a semantic-only profile. Assistance
must pass the [semantic paired gates](docs/vietnamese-semantics.md) after the
semantic-only stage; off/observe parity and classifier acceptance remain separate.

`src.cli.prepare_intents` provides JSON-configured validate/preflight/run batches. Shared dataset validation and offline orchestration live in `src.intent.dataset` and `src.intent.preparation`; `src.intent.training` owns regularization selection on grouped training folds, separate threshold calibration and frozen holdout evaluation. The historical `scripts.train_intent` and `scripts.intent_contract` entrypoints delegate to those modules. Import/help/validation do not load ML runtimes. Preflight uses the same query token helper as retrieval without encoding; run reuses one local E5 instance, never connects to PostgreSQL, and writes atomic files in a new private run directory. Manifest completion distinguishes a valid diagnostic run, a quality-passed candidate and an incomplete/failed run; it never authorizes runtime usage.

`scripts.benchmark_intent` remains separate and uses the public filtered retrieval path for paired off/observe checks, emitting an accepted artifact only after quality, export parity, retrieval invariance and latency gates pass. Runtime identity binds E5 files/revision and preprocessing; classifier evaluation does not replace corpus-matched passage calibration. Entity extraction, conversation state and Type B execution remain planned. See [intent observation and verification](docs/intent-classifier.md).

## Relevance filtering and empty fallback

Saved offline benchmark collections can be inspected with `scripts.diagnose_retrieval` without model inference or database access. It emits a metadata diagnostic and a separate private unranked source worksheet, retains unresolved labels, and never calibrates, changes runtime responses or issues a profile. Current package source hashes support reproducibility but do not independently certify historical database identity. See [diagnostic procedure and limits](docs/online-reranking.md#diagnose-empty-results-before-recalibration).

The later user-authorized [clear-content publication](docs/chunk-coverage-verification.md#clear-content-publication-follow-up--2026-11-13) expanded the local database to 28 current active publications / 524 active chunks, adding 200 chunks from fifteen formerly held packages. User-confirmed metadata and explicit source exclusions make the retained subset eligible; original snapshots and identities remain unchanged. This operation changes package data and publication state, not runtime code, schema or API contracts. Publication does not establish a matching relevance profile.

As of 2026-11-13, public retrieval uses top-K as an upper bound after per-passage relevance filtering. The fixed 30-candidate reranking pool is independent of top-K. A schema-v2 profile binds model/pipeline identity and the version-2 corpus fingerprint, including compatible active publications and retrieval-affecting metadata read in the same snapshot. Attempts and audit timestamps are excluded; old corpus fingerprints require new evaluation rather than rewriting. Missing, stale or legacy calibration withholds candidates with `no_matches/relevance_unavailable`; low-scoring candidates yield `below_relevance_threshold`. Eligibility and candidate absence retain distinct reasons. Operational errors remain errors. The offline benchmark retains a separate diagnostic candidate path for source labeling. HTTP accepts no request-level bypass; server configuration can explicitly disable relevance filtering. The CLI keeps the calibrated gate enabled. See [filtering and rollout](docs/online-reranking.md). No relevance score establishes full answer coverage.


The HTTP settings adapter reads `RELEVANCE_FILTER_ENABLED` (strict case-insensitive boolean, default `true`) and passes it into `RetrievalService`; direct callers may set `relevance_filter_enabled=False`. The public finalization path always enforces top-K and removes candidate diagnostics. When filtering is disabled it does not load/check the profile, returns unassessed reranked candidates (or vector candidates when reranking is off), and includes the `relevance_filter_disabled` warning. Empty reasons, model/database failures, locking, cancellation and intent observation remain unchanged. Offline `assessment=False`, CLI and calibration defaults are separate and unchanged. See [filter-mode contract](docs/online-reranking.md#retrieval-and-response-semantics); the backend currently discards relevance metadata, so the portal cannot distinguish this mode.
