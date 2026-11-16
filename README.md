# Rita Bot

Implemented: Python database foundation, agent-authored multi-format chunk packages, structural validation, transactional PostgreSQL publication, public-document retrieval through a function/CLI and local FastAPI, and health-only gRPC. E5-small runs directly in Python; retrieval returns source chunks. A separate opt-in grounded answer API supports reviewed filtered answering and explicitly unfiltered trials with Qwen3.5-0.8B Q5_K_M; trials retain unassessed relevance metadata and do not establish acceptance. Business RPCs, authenticated Type B tools and conversation history remain planned.

Source documents remain in `assets/`. Git-trackable current packages live at `chunks/<document_id>/`, containing `metadata.json`, `extraction.json`, `chunks.json` and an unchanged source snapshot. Agents author transcription and semantic chunks; Python initializes packages, manages revisions, validates, embeds with an explicitly supplied local model and publishes. There is no approval step or reviewer identity. Validation checks consistency, not transcription accuracy or business correctness.

Each source has at most one current package. Editing chunks keeps the source revision; changed source bytes require `python -m src.cli.prepare_documents revise --package chunks/<document_id> --source <relative-source-path> --supersedes-current`. Revision identity stays in metadata while the directory remains stable. Git preserves committed package history; `.gitattributes` preserves exact bytes across checkouts. Each environment still needs its own local model and explicit publication to its own database. See [layout migration and deployment](docs/offline-indexing.md#layout-migration-and-deployment) before using an older checkout's packages.

See the [indexing guide](docs/offline-indexing.md) for schemas, commands, metadata requirements and explicit non-destructive conversion of existing review/approved packages. Historical `indexing_chunks/` is untouched and never scanned. Model environments and authorization to process internal content must be established before agent reading; no hosted processing is implied.

The Offline Indexing Pipeline has two scopes. Scope 1 prepares sources and authors a complete package, with manual source/coverage checks and structural validation; no embedding model or database is required. Scope 2 reads that package, checks the actual tokenizer/model through preflight, embeds and publishes to PostgreSQL. Follow the [Scope 1 authoring procedure](docs/offline-indexing.md#agent-authoring-procedure) for focused detail chunks, source-backed inventories and optional groups within schema v2, or explicit evidence structures in schema v3. See [structured evidence](docs/structured-evidence.md). Author-declared coverage requires source review.

Before publication, use `python -m src.cli.preflight_documents --package <package-path> --model-path <local-model-directory> --model-revision <40-hex-commit> --json`. Install the optional local runtime from `requirements-example.txt` first. Preflight loads the local model and checks every chunk's token length without encoding by default; add `--check-embeddings` to generate and validate vectors in memory. It needs no database, accepts structurally valid hold/unknown packages, and never changes metadata or package files. Publication metadata blockers are reported separately. Passing preflight is not permission to publish or evidence of transcription/retrieval quality. Publication still performs its own checks; its `--dry-run` does not tokenize or embed. See the [preflight details](docs/offline-indexing.md#technical-preflight) and [Scope 2 operational guide](docs/offline-indexing.md#scope-2-package-reading-embedding-and-publication) for data mapping, outcomes and verification. Preflight vectors are discarded. Publication verifies stored data before skipping unchanged content; explicit metadata-only updates preserve the existing run/chunks. Changed publication reuses compatible stored exact-text vectors and encodes missing text; `--force` re-encodes every chunk. History remains stored within the new database; vector reuse itself needs no automatic republication. See the fresh-schema requirement below.

Entrypoints are in `src/cli/`, package processing in `src/ingestion/`, metadata validation in `src/catalog/`, database code in `src/database/`, tests in `src/tests/` and manual utilities in `scripts/`. `main.py` remains a thin database-check dispatcher. See the [application command reference](src/cli/README.md) and [manual utility reference](scripts/README.md) for command selection, status, prerequisites and effects. Source rename manifests, journals and `rename-history/` remain historical evidence.

The current schema head is `0006_publication_baseline`, intended for a **new empty database**. It refuses application tables containing data; no conversion of an existing corpus is supplied. Publication now tracks execution attempts, audits metadata and stores normalized evidence links. See the [database design](docs/database-design.md) before provisioning; keep existing database/model/package/evaluation inputs separate.

See [ARCHITECTURE.md](ARCHITECTURE.md) and [AGENTS.md](AGENTS.md).

Offline corpus preparation on 2026-11-13 covers 28 PDFs / 334 physical pages, including twelve software manuals and the labor rules. See [package coverage and verification](docs/chunk-coverage-verification.md) for authoring changes, local-model evidence, held metadata and publication limits. Prepared packages are not automatically available through the API; database publication and corpus-matched relevance calibration remain separate operations.

Subsequent PostgreSQL publication on 2026-11-13 verified 13 eligible packages / 324 active vectors: 12 identical-run skips and one new benefits run (29 vectors). At that publication snapshot, fifteen packages / 296 chunks remained held; this is historical, not the current on-disk eligibility inventory. Real-database regression retrieval found required top-5 evidence for 51/53 supported evaluated cases; 77 cases requiring held sources were deferred. See [publication checks and limits](docs/chunk-coverage-verification.md#postgresql-publication--2026-11-13). No relevance profile or API rollout was performed.

## Current verification status

### Explicit unfiltered answering - 2026-11-16

Working tree based on clean `900b00a`. The user-authorized combination
`ANSWER_ENABLED=true` / `RELEVANCE_FILTER_ENABLED=false` now permits native
answer generation without a relevance profile or answer report/review. Reranking
remains independent. The filtered branch retains its existing acceptance and
corpus checks; official offline evaluation still rejects unassessed candidates.
Normal answer responses add server-owned `relevance` metadata and retain
`unassessed`, `relevance_filter_disabled` and completeness warnings even after
successful verification. See [the unfiltered contract](docs/answer-evaluation.md#explicit-unfiltered-answering).

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
See [native setup and cancellation limits](docs/answer-evaluation.md#native-python-runtime).

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
See [answer behavior and acceptance](docs/answer-evaluation.md#local-answer-api).

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
remain regression data. See [lexical enrichment](docs/vietnamese-semantics.md#reviewed-lexical-enrichment-v2---2026-11-16).

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
source acceptance, not runtime release. See [source reconciliation](docs/vietnamese-semantics.md#explicit-source-reconciliation---2026-11-16).

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
[agent-assisted review](docs/runtime-artifacts.md#agent-assisted-source-review).

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

Post-edit investigation started from clean `cfb2e00`. Added `scripts.diagnose_retrieval`, an offline saved-collection inspector with a metadata diagnostic and a separate private unranked source worksheet; no public API, runtime ranking or profile acceptance behavior changed. See [diagnostic procedure](docs/online-reranking.md#diagnose-empty-results-before-recalibration) and [utility usage](scripts/README.md#evaluation-and-experiments).

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

The annotated [environment example](.env.example) groups database, transport, model and retrieval settings, with value limits and provisioning requirements. Preserve local values when updating comments; process environment variables override `.env`, and running services need a restart to load changes. `RERANK_PROFILE_PATH` is a commented example, not a supplied artifact: provision a validated corpus-matched profile through the [reranking workflow](docs/online-reranking.md) before expecting accepted excerpts with filtering enabled. The HTTP-only `RELEVANCE_FILTER_ENABLED` switch defaults to `true`; explicitly setting `false` returns unassessed top-k candidates without a profile and leaves reranking independent. CLI/evaluator defaults and calibration requirements are unchanged; see [filter modes](docs/online-reranking.md#local-model-and-configuration).

Runtime model files use durable storage under `models/embeddings/` and the existing root reranker layout. A selective Git allowlist and Git LFS preserve the pinned model inventory; versioned accepted runtime JSON can be tracked under `artifacts/`. After checkout, install Git LFS, run `git lfs pull`, then `python -m scripts.verify_runtime_assets` before model startup. `.tmp/`, download caches, experiment reports and machine receipts remain ignored. No accepted profile is included merely by enabling this storage policy. See [runtime artifacts](docs/runtime-artifacts.md) for checkout, artifact promotion, verified copying and independent backup/restore. Do not clear old temporary model copies until validation and recovery are complete.

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

Install `requirements-dev.txt` and `requirements-example.txt` in the Python 3.14 repository virtual environment. Retrieval loads E5-small directly in Python on CPU and uses no generative model; the optional answer route loads GGUF directly through an optional native Python binding. The application never downloads weights or falls back to hosted inference.

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

Optional `assist` requires `SEMANTIC_MODE=expand` and uses accepted style hints
only to prioritize valid phrase alternatives. It has its own pipeline/profile
identity and release gate after semantic-only evaluation. Source routing and Type B
execution remain unavailable. See [semantic workflow](docs/vietnamese-semantics.md).

Install `requirements-intent.txt` only for offline training. `python -m scripts.train_intent --help` describes training/calibration; `python -m scripts.benchmark_intent --help` describes the paired read-only benchmark required before promotion. Follow the guide for reviewed private datasets, model identity, quality/latency gates and rollback. No command automatically changes `.env`, downloads models, migrates storage or publishes documents.

For Stage 2 batch operation, use `python -m src.cli.prepare_intents validate --config .tmp/intent-v1/config.json --json`, followed by `preflight` and then `run` with the same config. See [configuration and batch outputs](docs/intent-classifier.md#unified-offline-preparation-cli) and the [example](docs/examples/intent-preparation.json). Validation aggregates structural errors without loading ML; preflight tokenizes without encoding; run requires reviewed labels and writes a new manifest/report/candidate directory. No database is required. The old trainer uses the same shared implementation and remains supported; benchmark/promotion are separate.

### Stage 2 batch CLI verification — 2026-11-13

Working tree based on `e656991`, after adding the shared offline preparation CLI: **537 passed, 1 skipped, 34 deselected** in the full non-integration suite, with the existing Starlette/AnyIO deprecation warning. After the final run-directory ownership/cleanup adjustment, all **20 batch CLI tests passed** again. Tests used the isolated extended-length `.tmp/intent-test-runs` workspace outside the sandbox. Coverage includes no-ML imports/help, aggregated validation, review-before-model gating, token-only preflight, legacy/new artifact parity, unique run directories, atomic manifests, interruptions, changed inputs and failure reporting. `pip check`, legacy/new CLI help and scoped diff checks passed.

Actual CLI validation accepted the structure of the 600-case draft while reporting `review_required: true` and `training_allowed: false`. Real local CPU E5 preflight checked all 600 questions without encoding; none exceeded the token limit. The report is under `.tmp/intent-v1/preflight-cli-report.json`. An actual `run` invocation correctly stopped before model loading with `human_review_required`, leaving a diagnostic manifest and no candidate. No reviewed training, semantic quality evaluation, database benchmark, accepted artifact or observation enablement was performed. Existing dataset/chunk changes and `.env` were preserved.

### Intent verification — 2026-11-13

Implementation working tree based on `e656991`, preserving existing documentation, corpus and answer-evaluation changes. Full non-integration verification after implementation: **516 passed, 1 skipped, 34 deselected**, with one existing Starlette/AnyIO deprecation warning. After final dataset-validation/diagnostic hardening and one additional test, the focused intent suites passed **35 tests**. Tests used a separate extended-length `.tmp/intent-test-runs` directory outside the sandbox; the first sandbox attempt failed at pre-existing temporary-directory permissions. No permissions or existing temporary trees were changed. Synthetic tests cover NumPy/sklearn parity, training/holdout separation, acceptance gates, off/observe retrieval invariance, HTTP/CLI wiring, encode counts, cancellation and cleanup. CLI help and `pip check` passed.

The private draft `.tmp/intent-v1/draft-cases.json` contains 600 structurally checked, **unreviewed** cases; training rejects it before loading E5. Human label/split review, real-model intent training, the quality gate and paired retrieval latency benchmark have **not run**. No accepted intent artifact has been produced, and observation has not been enabled. Database integration tests were deselected; no migration, publication, source-package edit, backend or portal change is part of this implementation. See the guide for the draft's template/diversity limitations and the independent retrieval calibration prerequisite.

<a id="offline-answer-evaluation-endpoint-not-implemented"></a>
## Grounded answers and offline acceptance

`POST /api/v1/answer` is a separate, opt-in local endpoint; `/api/v1/retrieve`
remains excerpt-only. Answering defaults to disabled. With filtering enabled it requires a matching,
manually reviewed 60-case study with explicit user acceptance. With filtering
disabled it may use marked unassessed candidates without a profile or study,
while retaining technical model checks, source validation and the verifier. It loads the local
`models/Qwen_Qwen3.5-0.8B-Q5_K_M.gguf` directly inside Python through
`requirements-answer.txt`, using one dedicated model thread and the existing
API port. It uses configured retrieval, source-backed claims,
and at most one generator/verifier call each. There is no conversation storage,
automatic repair/retry, backend route or portal integration.

See [answer API and acceptance](docs/answer-evaluation.md#local-answer-api) for
configuration, response fields, lifecycle, failure behavior and rollout. Passing
synthetic tests does not establish real-model quality, latency or production
readiness. Historical 40-case reports and Q4 model results cannot enable Q5.

### Fallback verification - 2026-11-13

Implementation working tree, including pre-existing reranking changes: the public function/CLI/HTTP path now filters each passage before top-K and returns a required nullable `fallback_reason`. Uncalibrated vector-only/reranked candidates are withheld. See [the response and rollout contract](docs/online-reranking.md#retrieval-and-response-semantics). No source packages, publications, schema, model files or application `.env` were changed.

Non-integration verification: **454 passed, 1 skipped, 34 deselected** using an isolated extended-length `.tmp/` basetemp outside the Windows sandbox. Earlier sandbox runs hit access-denied temporary-directory errors; no permissions were changed. The new per-passage 100-question source-labeled benchmark has **not been completed**: the available historical 40-question set lacks per-candidate labels and has related intents across splits. No schema-v2 profile was emitted, and no real-model quality/latency gate is claimed. Subsequent focused checks passed: 62 retrieval/API tests after profile hardening, then 24 reranking/benchmark tests after adding two orchestration cases. The pinned local ONNX smoke passed on two fictional passages without database access. PostgreSQL integration tests were not executed; synthetic tests do not verify the new corpus snapshot query on a real database.

### Online reranking verification — 2026-11-13

Historical top-1 evaluation, predating per-passage filtering (not a current rollout approval). Executed on the implementation working tree with the downloaded ONNX verified against official revision `1427fd652930e4ba29e8149678df786c240d8825` and its matching local tokenizer. A read-only, package-pinned 40-question evaluation (30 supported, 10 unsupported; known prompts kept as regression) found complete top-5 evidence for all 30 supported cases, with no candidate-pool misses. The required bereavement-family top-1 case failed: its detail ranked fourth. The calibrated relevance gate also failed (holdout positive top-1 recall 75%; calibration positive top-1 recall 46.7%; no holdout negative acceptances at that threshold). No calibrated profile was emitted; reranking remains disabled by default.

On 100 sequential post-warm-up requests with 30 candidates and batch size 4, p50 was 2.59 s and p95 was 3.88 s. Peak process working set was 1,951,526,912 bytes (about 1.82 GiB), including E5 and ONNX. Host: Windows x86-64, Intel Family 6 Model 165, Python 3.14.10. These observations are not a deployment SLA or evidence of complete policy coverage. Selected packages/storage passed before/after read-only verification. Local reports/cases are under ignored `.tmp/online-evaluation/`; private questions and source contents are not committed. The benchmark preceded final profile-fingerprint hardening (batch/runtime identity), which does not change candidate selection or inference; its profile fingerprint is intentionally not reusable by the final implementation.

Final non-integration verification: `python -m pytest -m 'not integration' -q` with a fresh extended-length `.tmp/` basetemp on Windows: **442 passed, 1 skipped, 34 deselected**. An initial ordinary-path run encountered 17 legacy asset-restructuring fixture errors from Windows path length; those resolved with the documented extended-length path, without changing the fixtures. Focused retrieval/API tests and the real local ONNX smoke passed. The updated PostgreSQL integration cases were **not executed**: automatic approval review rejected migrations/writes against the configured test URL because disposable ownership had not been verified. Read-only evaluation does not replace those transaction/eligibility fixture tests. No publication, schema change or stored-vector update was performed.

## Retrieval evaluation

Use `python -m scripts.evaluate_retrieval --package chunks/<document_id> --cases .tmp/evaluation/cases.json --model-path <local-model-directory> --model-revision <40-hex-commit> --top-k 5 --output .tmp/evaluation/report.json` for read-only evaluation of a published package. Package and cases paths are required; top-K defaults to 5 (1–20). Reports may only be written under `.tmp/`, outside the package/model and without overwriting the case file. The evaluator reads revision identity from metadata and rejects stale runtime-content/model fingerprints, current metadata/access/relation mismatches, ineligible revisions, changed inputs or stored content/spans/vectors/evidence that differ from the selected package. Original run byte hashes may precede harmless formatting or explicit metadata-only updates; they are not treated as current runtime identity. It records ranks, distances, model-load/overall question timings and storage verification, without copying retrieved content or vectors into reports. These timings do not separate query embedding from database latency.

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

`MIGRATION_DATABASE_URL` optionally supplies an account with DDL permissions; when absent or empty, migrations use `DATABASE_URL`. Migrations run only through explicit Alembic commands, not at application startup. The current upgrade/downgrade require empty application tables and preserve the shared `vector` extension. Future runtime access should use permissions needed for application tables rather than migration privileges.

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
