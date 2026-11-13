# Rita Bot Architecture

## Implemented storage and ingestion

Python 3.14, async SQLAlchemy/psycopg and PostgreSQL/pgvector provide six Type A storage tables with explicit Alembic migrations. Engines remain lazy; entrypoint imports do not connect to the database. A local health-only gRPC transport and public-document retrieval function/CLI are implemented; chatbot business RPCs and answer generation remain planned.

Source documents stay in `assets/`. `src.paths` anchors Git-trackable current packages at `chunks/<document_id>/`. Metadata is the sole owner of source identity, including revision_id; the directory name is document_id. Source-path uniqueness is checked case-insensitively. Git stores committed package history, while each checkout has only the current package. Per-document OS locks protect reads against replacement; staging, recovery markers and displaced packages live under ignored `.tmp/`. An interrupted replacement blocks reads until explicit recovery. Locks coordinate Python commands, not external editors; stop editing while validation/publication runs. Each complete package contains three schema-v2 JSON files (metadata, extraction and chunks) and unchanged source bytes. Initialization may leave an incomplete package until the agent authors extraction/chunks. Historical `indexing_chunks/` is never scanned or merged.

Agent-authored transcription and semantic chunking are the primary workflow; automatic Python loaders/chunkers remain retired. Package creation and validation are database/model-free. Agents preserve raw transcription, source locations, character spans, explicit exclusions and warning decisions. Image-recognized text is marked `ocr`, with the actual method recorded rather than an inferred engine identity. Structural validation cannot establish visual fidelity, transcription completeness or business correctness. There is no approval state or human approval command.

Publication reads a package into memory, validates eligibility, fingerprints all input bytes and embeds exactly that snapshot using an explicitly installed local model. It rechecks the package before writing and under the database publication lock. Metadata, access rules, revision relations, chunks/vectors and active-run changes share one transaction. Existing metadata changes need `--update-metadata`. For an existing revision, document ID, source hash, size and extension are immutable; the runtime currently allows source-path metadata changes with that flag when no collision exists. This does not rename the source file or replace the agent authoring rule to preserve identity and create a new revision for changed bytes. Explicit supersedes relations control revision replacement. Local revise requires `--supersedes-current` and keeps the declared ancestor list, so an environment can upgrade across skipped source revisions. Missing supersedes targets are recorded in run configuration and reported, without creating placeholder database rows; other missing relation targets still reject publication. There is no catalog-wide retirement or rechunking.

An independent, database-free technical preflight loads structurally valid packages without requiring publication eligibility. It shares local model loading, input preparation, token counting, encoding, vector validation and model fingerprinting with publication through `src.ingestion.embeddings`. By default it loads the model and checks tokens only; optional full-package embedding checks discard vectors after validation. Reports contain counts, fingerprints and separate metadata blockers, never source text or vectors. Successful checks recheck package/model bytes, create no durable approval state and do not bypass publication checks. Database conflicts and relation targets are outside preflight. Model commits remain declarations, not authentication of the local model files.

Run configuration retains source/content/metadata/package fingerprints and model configuration, without new approval fields. Equal configuration skips unless forced. Old successful runs retain their original JSON configuration. Pre-transaction failures produce stdout diagnostics without persisted failed-run records and preserve the previous active run. Local models are externally supplied, without downloads or managed caches. Dry-run checks packages, model fingerprints and current database state but does not tokenize or embed chunks.

Metadata requires include/active state, applicability, access groups and valid effective dates for publication; owner and evidence notes are optional. Access metadata does not implement future backend authorization. Migration `0003_agent_packages` removes review status and renames review notes while preserving storage history. Downgrade restores pending status and cannot recover old review values.

Existing review/approved packages are excluded from normal scans. An explicit preview-first converter validates the selected legacy package and writes the new layout without modifying or deleting originals. Conversion also applies stricter v2 extraction/chunk validation, so some valid legacy locations (for example XLSX without a sheet name) are rejected rather than inferred or silently repaired. Historical destructive workspace migration is retired. See the [package contract](docs/offline-indexing.md) for conversion, schemas and rollout.

## Offline authoring boundaries

Scope 1 ends at the complete, source-checked and structurally validated package. Scope 2 consumes it for actual tokenizer/model preflight, embedding and transactional publication. Model checks are not required to author a package, and source fidelity remains an authoring responsibility even when technical checks pass. See the [authoring procedure](docs/offline-indexing.md#agent-authoring-procedure).

Within schema v2, authors can compose detail, inventory and source-defined group chunks using the existing content and source references. These levels are conventions in text, not runtime types. Detail chunks retain necessary eligibility and exceptions while avoiding unrelated repeated material. Inventories have explicit source boundaries and preserve main-item/subitem labels; neither chunk count nor top-K matches establishes a complete benefit count.

Machine-readable chunk types, stable source-item identities, parent/child links and coverage tracking remain [future extensions](docs/offline-indexing.md#future-extensions--not-implemented). No schema or database change is implemented by this guidance. Optional authoring notes are not embedded or stored as chunk fields; document revision relations do not connect chunks. Online evidence expansion, completeness checks and fallback remain separate work, and improved authoring must still be evaluated with real retrieval questions.

## Offline publication boundaries

[Scope 2](docs/offline-indexing.md#scope-2-package-reading-embedding-and-publication) reads validated v2 packages, performs local-model checks and publishes atomically per revision. Preflight is independent and leaves no reusable vectors or passed-state prerequisite; database-aware dry-run does not tokenize or encode. Publication embeds the complete chunk content with `passage: `, stores that content without the prefix, and preserves resolved source spans. It does not derive runtime chunk types or hierarchy from authoring conventions.

The encoder requests normalized 384-dimensional vectors; validation checks count, indices, dimensions and finite values, not independent unit norm or semantic accuracy. Publication fingerprints the package/model, checks concurrent changes and skips identical active configurations unless forced. Current revision, active run, policy eligibility and retrieval model compatibility remain distinct conditions. Separate embedding text and structured item/coverage storage are future design work, not changes to the current contract.

## Implemented local gRPC transport

`src/helper/grpc` owns explicit configuration and an instance-scoped `grpc.aio` server. `python -m src.cli.serve_grpc` runs it through the existing Windows-compatible async runner. Imports and server construction do not load `.env`, create an async server or bind a port. Server creation, health registration and binding occur during `start()` on the owning event loop; lifecycle operations must remain on that loop.

The intended inbound direction is monolithic backend to chatbot microservice. Currently only the standard gRPC Health Checking service is registered. Overall health (`service=""`) becomes `SERVING` after startup and `NOT_SERVING` on graceful shutdown. It reflects transport availability only, without database/model checks. Shutdown waits up to the configured grace period and releases the listening socket.

The initial server is plaintext and restricted to `127.0.0.1`, with no authentication or business data. It supports same-machine development only. Public configuration rejects port zero and non-loopback binds; tests privately request ephemeral ports. Configuration, startup and shutdown failures produce nonzero CLI exits. See the [local server instructions](README.md#local-grpc-server) for configuration and a health call.

Remote/container connectivity, business RPC contracts, TLS/mTLS and authentication require a separate implementation. This inbound server does not implement outbound Type B access or change the database schema.

## Implemented public-document retrieval

`src.retrieval.RetrievalService` accepts an independent question string and returns nearest-neighbor chunks with source provenance. The local `src.cli.retrieve_documents` entrypoint uses this service; no chat RPC, HTTP endpoint or LLM answer generation is implemented. The service loads and fingerprints a local embedding model once, embeds questions with `query: `, checks token limits without truncation and validates/normalizes the 384-dimensional query vector. Model work is serialized in worker threads; each database request owns a separate read-only repeatable-read session. The application closes the shared database at shutdown, independently of service close. Runtime model files are immutable for the service lifetime.

Exact cosine retrieval filters current include/active revisions, inclusive effective dates using the same `date.today()` convention as publication, public access rules and active successful runs before ranking. Model name, revision, dimensions, file fingerprint, normalization and indexed passage prefix must match. Public access uses EXISTS to avoid duplicate matches; ties use chunk IDs. Results include source spans and document metadata, never vectors. Empty eligibility, model incompatibility, absent vectors and operational failures remain distinct. There is no similarity threshold: returned candidates do not establish sufficient evidence, truth or answerability. Private-document authentication, conversation resolution, LLM context construction and answer generation remain future work.

Synthetic unit tests cover query input, vector checks, model lifecycle and safe errors. Dedicated disposable PostgreSQL integration tests cover ranking and eligibility filters; they must not use the application database. Real-model retrieval evaluation is a separate read-only exercise against an explicitly selected dataset, measuring expected-source recall and latency without updating packages or database rows.

## Planned answering and operational retrieval

Type A documents provide policy evidence; Type B tools provide authoritative current operational facts. Preserve applicability, access restrictions, source citations and explicit unavailable/conflicting-source handling. Exact lookup/computation is needed for structured questions; the stored chunks and vectors alone are not a table query engine. Models are locally operated; internal data must not be uploaded as an implicit implementation choice.

### Type B: current operational evidence

Resolve the authenticated user and required entity identifiers, validate parameters and permissions in the backend, and call a defined read-only function. Initial examples are retrieving a person's direct manager, active project memberships, or a project's responsible person. gRPC is the intended integration option; the external contract and authentication mechanism are not yet defined.

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

| Phase | Deliverable | Validation focus |
| --- | --- | --- |
| 0 — implemented | Database foundation, explicit migrations, package-owned Type A metadata, agent-authored JSON batch ingestion, atomic validated-package publication and local health-only gRPC transport | Database behavior; source identity, consistency, history and metadata validation; local gRPC health and lifecycle |
| 1 — partial | Public retrieval function/CLI implemented; answer generation, answer citations and follow-up handling remain planned | Correct evidence, extraction quality, exact table answers, obsolete-document handling, unsupported B requests |
| 2 — planned | Authenticated, authorized Type B read-only tools | Identity/entity resolution, permission enforcement, freshness, unavailable services |
| 3 — planned | Combined A/B questions | Rule-to-person resolution, ordering of dependent calls, conflicting or incomplete evidence |

Use representative Vietnamese questions, paraphrases, follow-ups, ambiguous requests, and unanswerable questions. Measure retrieval quality and answer grounding separately, alongside latency and resource use. Phase 1 must not claim live personnel/project knowledge before Phase 2 exists.

Production model selection, hardware sizing, API framework, additional schemas, chunking parameters, ranking configuration, authentication integration, gRPC contracts, and quantitative acceptance targets remain decisions for implementation planning. The current six-table schema and 384-dimensional embedding column establish storage only. Fine-tuning should follow a measured need rather than be an initial prerequisite.

## Verification boundaries

Unit tests cover synthetic package contracts, converter compatibility, CLI batch ordering and publication boundaries; gRPC tests exercise local health RPCs. They cannot establish transcription accuracy or real embedding-model behavior. PostgreSQL integration tests use guarded disposable databases and synthetic embeddings to exercise publication, revision relations, transaction rollback and migration preservation. They perform schema changes and application-table writes/cleanup, including downgrade/upgrade; see [test setup and safety](README.md#testing). Missing configuration means skipped verification, not a successful database check. Historical rename-utility tests remain compatibility checks outside the v2 ingestion workflow.
