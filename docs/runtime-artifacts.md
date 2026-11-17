# Runtime artifacts and calibration operations

Runtime dependencies must survive deletion of `.tmp/`. Keeping files outside Git does not make them disposable or provide a backup. This guide owns local storage, verification, recovery and profile promotion; [online reranking](online-reranking.md) owns calibration semantics and quantitative gates.

## Local layout

| Location | Role | Lifecycle |
| --- | --- | --- |
| `models/embeddings/multilingual-e5-small/<revision>/` | Pinned E5 model files | Immutable while running; provision before startup |
| `models/` | Existing pinned reranker ONNX and tokenizer files directly in this directory | Selective Git/LFS inventory; preserve the supported layout and file bytes |
| `artifacts/rerank/` | Accepted `relevance-profile.json` and provenance manifest; full reports stay in private durable storage | One selected current pair; history in Git |
| `artifacts/semantics/` | Reviewed corpus-bound language lexicon and provenance | Provision only after review and matching pipeline evaluation |
| `artifacts/answers/` | Source-bound `catalog.json` and provenance | One current pair, compatible with the selected lexicon |
| `artifacts/storage-preparation-<date>/` | File inventory and migration/smoke receipts | Local operational evidence, not an independent backup |
| `.tmp/` | Evaluation workspaces, private draft cases, candidates, caches and package staging | Not a runtime dependency; reviewed evaluation inputs still need backup |
| `artifacts/runtime-assets.json` | Versioned model identities and selected file inventory | Track in Git; verify before startup and update explicitly for a new release |

Git allows only the pinned 10 E5 files, the six supported root reranker files, the runtime inventory, selected current runtime JSON and the explicitly declared lexicon/catalog authoring sources below. `.tmp/`, model download caches, machine receipts and experiment reports remain ignored. Do not commit real questions, additional source excerpts outside the existing source-bound catalog contract, credentials or `.env`. Optional accepted intent artifacts may live under `artifacts/intent/`; do not create that directory or enable observation/assistance before the separate intent gates pass. Experimental answer-model paths under `.tmp/` are outside this inventory. A semantic lexicon's hash participates in the effective reranker fingerprint; keep each lexicon, classifier (if assistance is enabled) and relevance profile as a compatible release set. See [semantic preparation and release](vietnamese-semantics.md).

The API defaults to the durable E5 path above. An explicit `E5_MODEL_PATH` still overrides it, including legacy paths; there is no silent fallback to the old directory. API environment paths resolve against the repository root, and HTTP startup gives `.env` precedence over process environment; missing keys use process environment, then defaults. The retrieval CLI uses explicit model/rerank arguments. Restart existing services explicitly after changing their configuration.

## Split-source authoring

Edit only these source inputs for the selected lexicon/catalog, keeping each
concept/card intact with its aliases, guards, sections and citations:

```text
artifacts/semantics/source/index.json
artifacts/semantics/source/concepts/<concept-id>.json
artifacts/answers/source/index.json
artifacts/answers/source/cards/<card-id>.json
artifacts/answers/source/coverage/<document-id>.json
```

Each index has `schema_version: 1`, `metadata` and ordered relative-path arrays:
`concepts` for semantics, `cards` and `coverage` for answers. Semantics metadata
contains exactly the runtime `schema_version`, `reviewed`, `review_notes` and
`package_fingerprint`; answers metadata contains exactly `schema_version` and
`package_fingerprint`. Source and runtime schema versions are separate contracts.
For example a concept reference is `concepts/annual-leave.json`. The arrays
determine output order; directory discovery never selects additional content.
Each concept/card file is the existing object, with an ID matching the filename.
Each coverage file is a nonempty array for its document, preserving chunk order.
Add/remove references explicitly when adding/removing files. IDs must be safe
portable filenames; case-colliding references, duplicate JSON keys/IDs, invalid
coverage indexes, unsafe paths, links/junctions and undeclared files are rejected.

```powershell
python -m src.cli.build_artifacts check
python -m src.cli.build_artifacts build
python -m src.cli.build_artifacts check
python -m src.cli.check_answer_catalog
python -B -m scripts.verify_runtime_assets
```

Both check/build assemble in memory and validate against the complete current
`chunks/` snapshot using existing semantic and catalog validators. They need
the ordinary Python application dependencies and local packages, but no model,
database, `.env` or network. `check` writes nothing and fails on missing/stale
runtime outputs. `build` writes only changed runtime files after validating both
outputs and rechecking source bytes/file membership. Unchanged files retain their
mtime. JSON is UTF-8 without BOM, indent 2 and a final newline; lexicon uses LF,
catalog uses CRLF to preserve the selected artifact bytes. Field and array order
are preserved. Catalog `lexicon_sha256` is derived from the assembled lexicon.
This is not a runtime memory optimization: existing loader size limits remain.

New candidates still come from explicit offline preparation; semantic export
continues to write only a private candidate. To import a compatible pair:

```powershell
python -m src.cli.build_artifacts import --lexicon .tmp/candidate/lexicon.json --catalog .tmp/candidate/catalog.json --output .tmp/new-artifact-import
```

Both input files must pass current source/lexicon identity validation. Output
must be a new directory below repository `.tmp/`; it contains
`artifacts/<group>/source/` trees. Review those trees before explicitly replacing
the selected sources in a release-preparation checkout. There is no automatic
candidate discovery or promotion. Input formatting is normalized on assembly,
so noncanonical candidate bytes can produce new hashes even with equal content.
A failed import can leave an incomplete private output; inspect it and use a new
output path for another attempt. Do not commit the private import workspace.

Build never edits either provenance manifest or `runtime-assets.json`. After
intentional content changes, review and reconcile their hashes/sizes, compatible
pipeline bindings and evaluation declarations through the existing release
workflow. Do not carry forward an acceptance claim invalidated by new content.
Stale inventory fails the separate inventory gate until reconciliation is done.
Passing source/build/catalog checks is not business review or runtime acceptance.

Runtime still consumes only the selected artifact/manifest pairs, not `source/`.
The inventory checker permits only paths declared by structurally valid source
indexes; these files are not runtime assets and have no entries in the runtime
inventory. It rejects extra on-disk files inside source trees and undeclared
tracked paths. Its source checks are layout checks, not the full semantic or
source-bound catalog validation; always run `build_artifacts check` first.
The Git ignore allowlist exposes only indexes and the three JSON record folders;
the checker remains responsible for rejecting undeclared JSON files.

Build fsyncs staged outputs under `.tmp/` and replaces each changed output
atomically, after checking that targets have not changed. The pair replacement
is not a multi-file transaction. Never run it over a live service tree or edit
sources concurrently. After interruption, run build and check again in the
release-preparation checkout before release. Roll back source inputs, generated
outputs, provenance and inventory as one compatible Git release set in a
separate checkout. No service deployment/restart happens in these commands.

## Stable-path transition

Each checkout contains one selected compatible runtime set. Git commit/release
identity and manifest hashes identify its version; `schema_version` still means
file format. There is no directory scan, automatic latest selection or fallback.
The layout transition retains the exact catalog bytes formerly at `answers/v1`
and lexicon bytes formerly at `semantics/v2`; the catalog binds that lexicon by
SHA-256. The older `semantics/v1` is retained in Git history only. Both selected
manifests still declare runtime acceptance false. Layout/integrity validation
does not grant evaluation acceptance.

Before restarting a deployment using old paths, update only the applicable
assignments in its private `.env` or launcher configuration:

| Previous path | Current path |
| --- | --- |
| `artifacts/answers/v1/catalog.json` | `artifacts/answers/catalog.json` |
| `artifacts/semantics/v2/lexicon.json` | `artifacts/semantics/lexicon.json` |
| `artifacts/rerank/<version>/relevance-profile.json` | `artifacts/rerank/relevance-profile.json` |
| `artifacts/intent/<version>/accepted-artifact.json` | `artifacts/intent/accepted-artifact.json` |

A deployment using `semantics/v1` must deliberately select and validate a
compatible catalog/lexicon/profile set; do not merely redirect that setting to
the different current lexicon. Variable names remain `ANSWER_CATALOG_PATH`,
`SEMANTIC_ARTIFACT_PATH`, `RERANK_PROFILE_PATH` and `INTENT_ARTIFACT_PATH`.
Explicit custom paths remain supported by loaders, but tracked runtime assets
must follow the current inventory policy. Missing old paths remain unavailable.
This repository change does not edit private `.env`, enable features or restart
services.

Prepare the full release from a pinned commit in a separate checkout, hydrate
LFS, run the inventory checker and catalog validator, and verify profile/corpus
compatibility before switching the service to that checkout and restarting.
Deploy or roll back the compatible set and its configuration together, never
just the lexicon. An old profile still needs compatibility with the current
publication identities; a Git rollback cannot roll back the database. Historical
verification entries mentioning version directories describe earlier snapshots.

## Git LFS checkout and verification

Install Git and Git LFS before preparing a checkout. From the repository root:

```powershell
git lfs install --local
git lfs pull
python -m scripts.verify_runtime_assets
```

The pull retrieves model bytes from the repository's LFS storage during checkout/deployment preparation, never from application startup. Weights (`.safetensors`/`.onnx`), SentencePiece `.model` files and `tokenizer.json` use LFS. Small model configuration and the existing E5 README use ordinary Git. Byte-preserving attributes cover every selected file, including JSON, with `core.autocrlf=true`. Do not remove the E5 README, add receipts inside E5 or normalize file contents: the recursive runtime fingerprint includes every file. Keep LFS filters enabled; `-text` must not disable hydration.

The manifest has `schema_version: 1`, `models` entries for `e5` and `reranker` (model ID, revision, root and `files_sha256`), and `assets` entries (`path`, `size_bytes`, `sha256`, `group`). Paths are repository-relative with forward slashes. Each model's `files_sha256` is the canonical SHA-256 of its relative-file-name-to-SHA-256 map; E5 uses the existing runtime algorithm. The reranker file-map hash is **not** its pipeline fingerprint, which also includes configuration and ONNX Runtime version. Optional asset groups are `answers`, `rerank`, `semantics` and `intent`.

The checker is read-only and uses only Python's standard library plus local Git. It does not read `.env`, import ML libraries, load models, access a database, download or repair files. It rejects invalid/duplicate entries, unsafe paths or links/junctions, missing/changed files, unhydrated LFS pointers, extra files anywhere below E5, and tracked asset paths outside the inventory/allowlist. Ignored root model caches and operational receipts do not fail verification. Output contains paths, fixed error codes and file counts, never file contents. Exit codes: `0` integrity/policy passed, `1` asset mismatch, `2` invalid inventory or operational failure. `--manifest <repository-relative-path>` selects an alternative local inventory. Running with `python -B` also suppresses Python's normal bytecode-cache writes.

The checker reads tracked paths from the Git index. During this layout migration,
unstaged old-path deletions still produce `tracked_path_not_allowed`; include all
artifact additions/deletions in the reviewed index before the release check. An
isolated temporary index/object store can validate that intended state without
staging changes in the working repository.

Before the first model upload, confirm remote LFS support, storage quota and access. If unavailable, stop release preparation rather than committing large files through ordinary Git. No history rewrite is needed for the currently untracked models. This change does not push, configure CI, or supply a container/deployment bundle. A passing local checker does not establish remote availability, independent backup, model redistribution rights or a successful production release. Preserve upstream attribution and review model terms before distribution; do not insert extra license files into the existing fingerprinted E5 directory.

## Add or promote a version

For the user-approved learned-scoring experiment, schema-v3 relevance profiles
embed the exact evaluated coefficients. Use the gated local promotion utility
after the paired semantic benchmark and actual bound human review; see
[scoring release and recovery](relevance-scoring.md#independent-evaluation-and-release).
Development coefficients, source worksheets and training reports remain private
and are not substitutes for an emitted passing profile. Runtime loading against
the matching corpus/configuration must still pass before claiming end-to-end
completion.

1. Keep model preparation and all evaluator/training outputs in their existing offline workflows. Do not change benchmarks to emit directly into a tracked directory. Never mark a candidate accepted just to make storage validation pass.
2. For models, inventory the exact reviewed file set, preserve immutable revisions and update the explicit Git allowlist for a new revision. Keep model metadata/receipts outside model directories. Update the `models` identities and file entries in the runtime inventory from actual bytes; verify pinned reranker hashes against the runtime allowlist. New model identity may require separate publication/calibration work.
3. In a separate release-preparation checkout, place unchanged selected bytes directly at the stable paths in the layout table, alongside a matching `manifest.json`, and replace the corresponding inventory entries. For lexicon/catalog releases, also import/reconcile the selected authoring sources and run `python -m src.cli.build_artifacts check` before catalog/inventory validation; generated bytes must match the selected release. Each group has one current pair; Git preserves previous releases. Keep schema versions, content hashes, fingerprints and acceptance states unchanged for layout-only changes. Do not overwrite a live service tree or automatically promote the newest candidate. Do not create empty optional directories or placeholder artifacts.
4. The provenance manifest records artifact/report/frozen-input hashes, model/pipeline identity and relevant package/corpus identity, plus the reviewed release configuration. Keep raw reports, questions, source worksheets and machine-specific paths outside tracked artifacts. Review free-text provenance and lexicon content before committing; the integrity checker cannot certify acceptance or detect all private content.
5. Run the checker and review the selected Git diff, ignore rules and attributes. After an approved commit/upload, validate a fresh checkout with `git lfs pull` and the checker. Missing LFS objects must block preparation; never substitute generated files or silently download from upstream.

File integrity does not establish runtime compatibility. Semantic expansion requires the matching complete `chunks/` tree (JSON plus source snapshots), including currently held packages. A relevance profile binds database publication run IDs and configuration, not just document bytes; publishing the same packages independently in another database creates new IDs and requires a matching evaluated profile. Preserve database identity through an explicitly managed restore or repeat the existing calibration process against the intended corpus; do not rewrite profile fingerprints. This storage task does neither. `/health/ready` remains a dependency check, not a profile/corpus quality gate.

Historical provisioning snapshot (the old path is retained only in Git history):
on 2026-11-16 the user explicitly requested provisioning the source-approved
`artifacts/semantics/v1/lexicon.json` before completing retrieval evaluation. This
specific storage exception preserves the approved bytes and inventories the
lexicon/manifest pair. The manifest declares
`source_reviewed_provisioned_evaluation_pending`, with retrieval evaluation and
runtime release acceptance false. It supplies no relevance profile, evaluated
pipeline/corpus identity or quality claim. Environment configuration and services
remain unchanged; a matching accepted profile is still required for calibrated
semantic retrieval. Do not interpret inventory verification as evaluation approval.

### Current relevance-profile replacement and reconciliation

The gated promotion command retains `--version` as a provenance `release_version`
label, with its existing validation; it always writes `artifacts/rerank/`.
First provisioning requires no existing pair or inventory entries. Replacement
requires `--expected-current-sha256 <existing-profile-sha256>` matching the exact
current profile bytes. Missing/mismatched hashes, incomplete pairs, invalid
inventory or failed evaluation/review gates reject without replacing the pair.
The command never edits `.env`, deploys or restarts services.

Promotion stages the new pair/inventory and fsyncs exact old pair/inventory
snapshots under `.tmp/rerank-promotion-<id>/previous/` before replacing current
files. A cooperative `.tmp/rerank-promotion.lock` names the stage directory.
Inputs, current bytes and inventory are rechecked under the lock; each file
replacement is atomic, but the pair plus inventory are not one filesystem
transaction. Inventory is replaced last and postflight verifies the resulting
bytes. External writers must coordinate; use a release-preparation checkout.

After a partial/uncertain write, keep the lock and reconciliation receipt. Stop
other writers, compare the stage, `previous/`, current files and receipt hashes,
and explicitly reconcile either the complete new pair/inventory or the complete
previous pair/inventory. Verify inventory and compatibility before clearing only
the identified lock. For failed first provisioning, the previous snapshot has
no profile pair: remove only the newly created pair after confirming its paths
and hashes, then restore the previous inventory. Never retry, delete recovery
state or restore over a running service automatically. The local snapshot is
recovery evidence, not an independent backup.

## Copy, verify and recover

1. Record the source revision and inventory **every** model file by relative name, byte length and SHA-256. Inventory the pinned reranker files too when backing up the full runtime. Store receipts outside model directories: adding a receipt inside E5 changes its recursive file fingerprint.
2. Copy into a new, non-existing durable directory. Reject symlinks/junctions, do not overwrite a conflicting destination, and retain the source. Verify identical relative filenames, lengths and SHA-256 values; recheck the source after copying. Keep the E5 revision and model bytes unchanged so indexed identity remains compatible, without re-embedding or publication.
3. Update only the intended `.env` assignments and current defaults/examples. Check all unrelated parsed settings remain unchanged and keep `INTENT_MODE=off`. A missing profile must remain unconfigured rather than be replaced with a placeholder JSON file.
4. Load E5 from the new location, encode a fictional query and check 384 dimensions. Load the pinned ONNX/tokenizer and verify its startup smoke. Retain the original model until validation and the independent backup/restore check are complete. Do not clean up old `.tmp/` trees incidentally.
5. Obtain an explicit independent destination (another physical disk, external drive or controlled network share). Copy the model files, accepted artifacts, receipts and frozen private evaluation inputs into a new versioned backup directory. Do not include `.env` or credentials; manage credential recovery separately. A second directory on the same physical disk is only local redundancy.
6. Restore from that backup to a new scratch directory, compare the complete file inventory and load the restored model/profile there. Never restore over the running model. Record the destination, inventory hashes and restore results in a local receipt. Until this succeeds, report **independent backup unverified**.

Moving bytes alone is not proof that retrieval works. Confirm package/publication identity using read-only database checks and use the public retrieval path after a compatible profile is available. Migrations, republication and application-data writes are not part of this procedure.

## Prepare, review and freeze calibration

Use the source-review and split requirements in [online reranking](online-reranking.md#evaluation-and-rollout). The offline benchmark writes reports only under `.tmp/`; retain that interface. It does not consume `RERANK_PROFILE_PATH` as an existing calibration input: run it with explicit `--rerank`, model identity and matching batch settings, without `--rerank-profile`.

The 2026-11-14 task workspace is `.tmp/storage-calibration-20261114/`. Start with `HANDOFF.md`. `existing-label-review.json` presents the independent calibration task's 111 questions and 3,330 proposed boolean passage labels for user review, with source content/references and no ranking scores. Original frozen hashes and case-to-label mappings were verified; no user approval was inferred. That separate evaluation failed quality gates and emitted no profile. Do not tune holdout labels to its results.

The supplemental new draft contains `manifest.json`, package-pinned `cases/`, `source-review.json`, `REVIEW.md` and matching read-only corpus snapshots. Collection completed with 3,000 candidate pairs; `candidate-review.json` removes ranks/scores and preserves 2,941 unresolved labels alongside 59 direct-evidence proposals. It is not ready for a full benchmark. These are local, unreviewed artifacts. Source-extraction excerpts aid review; the unchanged original source snapshots remain authoritative. Unsupported cases mix disconnected live facts with out-of-corpus controls and need user review for realism and coverage. Structural validity and minimum split counts do not establish representative semantic quality.

```powershell
.\env\Scripts\python.exe -m scripts.benchmark_online_retrieval --manifest .tmp/storage-calibration-20261114/manifest.json --model-path models/embeddings/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3 --model-revision 614241f622f53c4eeff9890bdc4f31cfecc418b3 --rerank --collect-only --output .tmp/storage-calibration-20261114/candidates.json
```

Choose a fresh output name when repeating: the tool refuses overwrite. `--collect-only` never emits an accepted profile; an exit code of 1 with a written collection report means rollout gates did not pass, not successful calibration. Exit 2 is an operational/validation failure. Collection still performs the configured latency-request loop; it is not a cheap JSON-only preflight.

Before the full run:

- Review each question, its evidence groups, supported/unsupported status and source fidelity. Confirm that related intents, including duplicate-source/paraphrased cases, never cross calibration/holdout/regression splits.
- Review **every** collected candidate against the source. `candidate_labels` must map exactly the returned chunk UUID set to booleans. A required evidence chunk is not automatically the only relevant passage. Unresolved labels must stay unresolved; never default them to false or derive them from model scores.
- Keep holdout ranking scores out of question/label tuning. Record user approval, hash the manifest/case files and freeze inputs. The current benchmark does not enforce human approval itself: complete this operational gate before invoking it without `--collect-only`.
- Preserve the reviewed dataset and candidate worksheet in the independent private backup. Recollect and review again if candidate identities, packages, corpus or runtime configuration change.

Only after user review, run the same command without `--collect-only` and with a fresh report output, for example `accepted-evaluation.json`. A successful all-gates run writes `accepted-evaluation.profile.json` beside it. A report without a profile is a valid outcome when quality or latency fails; do not hand-author a threshold or set `holdout_passed` to true.

On 2026-11-16 the user accepted all final review scopes for the new 145-case /
4,350-pair dataset. Frozen full evaluation completed in the private
`.tmp/relevance-profile-20261116-05/` workspace against the isolated schema-0006
28-package corpus. It failed evidence and relevance gates despite passing
latency and read-only integrity checks. Therefore there is no emitted profile to
copy into `artifacts/rerank/relevance-profile.json`; that completion
criterion remains unmet. See the [current verification status](../README.md#current-verification-status)
for actual results. This is now a scoring-quality limitation, not pending label
approval or a missing copy operation. Preserve the accepted source snapshot and
newer workspace source edits separately. After outcome-driven scoring changes,
the inspected holdout is diagnostic data and cannot serve as a fresh acceptance
holdout; collect and review new disjoint acceptance cases for the changed
pipeline rather than changing the accepted labels or lowering gates.

### Agent-assisted source review

Agents may prepare independent label proposals to reduce review work. Give both
reviewers the same question, candidate text and source references without model
scores, ranks, historical candidate labels or the other reviewer's decisions.
Each reviewer must assess every candidate, preserve conditions and record a
source-based reason; unassessed or ambiguous pairs remain unresolved, never
default negatives. Required evidence groups are not an exhaustive relevance map.
Source-extraction excerpts support review but do not establish original-source
transcription accuracy.

Compare proposals by case ID and candidate UUID only after both independent
reviews are complete. Preserve disagreements and source limitations for the
responsible person's review of the complete proposed label set. Agent agreement
does not complete the human review declarations. Audit semantic families against
historical inspected questions as well as current splits; renaming a family or
randomly moving paraphrases does not create an independent holdout. Changes to
questions, evidence or splits require a fresh collection and candidate review.
Old-collection proposals remain diagnostic when preparing a revised dataset;
never rewrite collection bindings to make a new manifest pass freeze.

After source review by agents, an explicitly authorized `--collect-only` run
may collect diagnostic candidates while human acceptance is pending. Preserve
uncertain question/support hypotheses and all human-review declarations as
unaccepted. Review the resulting complete source/split/label packet before
freeze or full calibration; diagnostic collection never issues a profile.

Collection uses the application's current publication readers. Verify schema,
stored publication/evidence and model compatibility before inference. If the
configured database is incompatible, record the exact preflight blocker and
resume only on a compatible provisioned corpus. Calibration does not authorize
schema migration, data conversion, republication or a silent old-code fallback.

When separate provisioning is explicitly requested, initialize a new empty
database using the [current database design](database-design.md), then publish
the selected source packages through the existing publisher. Set both
`DATABASE_URL` and `MIGRATION_DATABASE_URL` to the same new target in child-process
configuration only; preserve the application database and `.env`. Never put
connection URLs in receipts. New run/chunk identities require a fresh collection
and corpus-bound calibration. A profile for that new corpus does not authorize
the old database or another independently republished corpus.

## Promote and verify

Profile promotion applies to the default `RELEVANCE_FILTER_ENABLED=true` mode. Explicitly setting it to `false` returns unassessed candidates and skips loading the profile; it does not create a profile or satisfy calibration gates. See [filter modes](online-reranking.md#local-model-and-configuration). Restore `true` and restart to restore the calibrated gate; without a matching profile it withholds candidates. The CLI and offline benchmarks retain their existing defaults.

For raw-max scoring, copy an emitted schema-v2 profile to `artifacts/rerank/relevance-profile.json`. Learned scoring uses schema-v3 with bound coefficients and the separate [gated promotion workflow](relevance-scoring.md#independent-evaluation-and-release); a fitted candidate alone is not eligible for release. Preserve the full evaluation report in private durable storage and commit only the selected runtime JSON plus a provenance `manifest.json` recording file hashes, report/frozen dataset/manifest hashes, E5 revision/fingerprint, reranker fingerprint, corpus fingerprint and exact runtime configuration. Add the pair to the runtime inventory and run the checker. Keep private cases/source excerpts in the private backup, not committed documentation.

Verify hashes and compatibility before setting `RERANK_PROFILE_PATH` to that existing file. Restart the API at an agreed time and exercise supported and unsupported questions through HTTP. Distinguish `relevance_unavailable`, `below_relevance_threshold`, `no_candidates`, `no_eligible_documents` and operational failures. Inspect counts/reasons/timings without recording question/document content in operational logs.

`/health/ready` retains its dependency-availability contract. A ready response does not prove profile/corpus compatibility, semantic quality or complete evidence. Perform the profile/retrieval checklist separately; no health or retrieval response schema is added by this storage change.

Corpus eligibility/publication changes or model/pipeline fingerprint changes invalidate reuse until compatibility is checked and calibration repeated where needed. Roll back only to an artifact compatible with the current corpus/runtime. Otherwise preserve unavailable assessment and withhold candidates; neither disabling reranking nor a high raw score authorizes an unfiltered fallback.

## Optional answer model and runtime

Qwen/GGUF answering is retired. Local model bytes and installed packages are
preserved, but no runtime path loads them. No accepted Qwen report can authorize
Semantic + template answers. The current runtime uses
`artifacts/answers/catalog.json` and its provenance manifest, inventoried under
the `answers` group. Source/lexicon identity validation is distinct from business
review and retrieval relevance acceptance. See the
[current answer contract](answer-evaluation.md#semantic-template-answer-api).
