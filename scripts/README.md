# Manual utility and offline experiment reference

This folder contains task-specific tools, offline evaluations, compatibility aliases and supporting modules. Supported application operations live in [src/cli](../src/cli/README.md). Tools stay here because of their role, not because they use Python or can run directly.

Run `python -m scripts.<name>` from the repository root with the repository virtual environment unless a direct-file invocation is explicitly listed. Angle-bracket arguments below are placeholders. Install only the optional dependencies required by the linked guide. No tool implicitly downloads models. Output under `.tmp/` is private local work, not acceptance evidence by itself.

## Inventory

| File | Status / role | Purpose |
| --- | --- | --- |
| `try_retrieval.py` | Current manual utility | Run the public retrieval contract from an IDE and display Vietnamese source excerpts/timings. |
| `verify_runtime_assets.py` | Current read-only release utility | Verify selected local model/artifact bytes, hydrated LFS files, the E5 tree and tracked Git paths against the runtime inventory. |
| `evaluate_retrieval.py` | Current offline evaluator | Evaluate one published package against pinned source-reviewed cases. |
| `benchmark_online_retrieval.py` | Current offline benchmark | Inspect offline candidates, calibrate per-passage relevance and measure quality/latency gates. |
| `benchmark_semantic_retrieval.py` | Current offline benchmark | Compare baseline/semantics/optional intent assistance across corpus-wide cross-document evidence cases. |
| `fit_relevance_scoring.py` | Experimental offline fitter | Fit development-only local passage scoring; emits coefficients and a training report, never an accepted profile. |
| `prepare_scoring_review.py` | Private offline review bridge | Create unranked source worksheets or export actually reviewed paired features into development rows. |
| `promote_relevance_profile.py` | Gated local release utility | Copy only an emitted, reviewed, passing semantic schema-v3 profile and provenance into a new version; update runtime inventory. |
| `diagnose_retrieval.py` | Current offline diagnostic | Inspect a saved benchmark collection and prepare unranked source-review worksheets; no model/DB access or profile issuance. |
| `benchmark_intent.py` | Current offline benchmark | Paired intent-off/observe checks and gated accepted-artifact output. |
| `evaluate_answers.py` | Experimental entrypoint | Offline Qwen3.5-0.8B Q5_K_M comparison and manual acceptance; does not enable the API. |
| `answer_evaluation_contract.py` | Experimental support module | Private case IO plus compatibility exports of shared answer schemas/gates; no standalone command. |
| `answer_evaluation_runtime.py` | Experimental support module | Compatibility exports of src.answering runtime/pipeline; no standalone command. |
| `train_intent.py` | Supported compatibility entrypoint | Delegates to `src.intent.training`; prefer config-driven `prepare_intents` for batches. |
| `intent_contract.py` | Compatibility support alias | Aliases `src.intent.dataset`; no standalone command. |
| `convert_chunk_package.py` | Supported legacy conversion | Explicit non-destructive v1 review/approved to v2 conversion. |
| `convert_structured_package.py` | Current opt-in conversion | Convert v2 to a v3 scaffold; semantic authoring still required. |
| `migrate_chunk_layout.py` | Supported legacy conversion | Select an old revision directory and migrate to the current package layout. |
| `materialize_noi_quy_packages.py` | Task-specific authoring utility | Serialize explicitly authored labor-rules segments, chunks and local evaluation inputs. |
| `normalize-assets.ps1` | Historical migration | Normalize asset filenames in the historical `app/` layout. |
| `restructure_assets.py` | Historical migration | Apply a fixed asset-folder mapping in the historical `app/` layout. |
| `migrate_indexing_chunks.py`, `migrate_indexing_workspace.py` | Retired entrypoints | Return replacement guidance, without performing migrations. |

## Manual retrieval

For asset verification before retrieval, install Git LFS, run `git lfs pull`, then `python -m scripts.verify_runtime_assets`. Optional `--manifest <repository-relative-path>` selects another inventory. The utility uses only standard-library Python and local Git, without reading `.env`, loading models, accessing a database, downloading or writing files. Use `python -B` to also suppress Python's normal ignored bytecode-cache writes. Exit codes: `0` integrity/policy passed, `1` asset mismatch, `2` invalid manifest or operational failure. JSON output contains paths, fixed error codes and counts. This is not calibration acceptance or live database/remote LFS verification. See [checkout and promotion](../docs/runtime-artifacts.md#git-lfs-checkout-and-verification).

Edit `QUESTION`, `TOP_K`, `MODEL_PATH`, `MODEL_REVISION` in `try_retrieval.py`, keeping UTF-8 encoding, then run `python -m scripts.try_retrieval` or execute the file in the IDE. Direct execution works from other working directories without changing them. Its configured local model directory must actually be provisioned.

Requires PostgreSQL, compatible published vectors and a local model. Reads public filtered results through `src.retrieval.runner`, shared with [the retrieval CLI](../src/cli/README.md#retrieval-and-services); does not implement another search algorithm or start HTTP. Output includes full accepted source excerpts and completed timings, without publication writes. Exit codes follow the runner (`0` valid results/empty outcomes, `1` selected input/model failures, `2` operational failure). The compact no-match display does not distinguish every fallback reason; use CLI JSON/HTTP for that. The editable default configuration does not enable calibrated reranking. See [retrieval evaluation](../docs/retrieval-evaluation.md).

## Evaluation and experiments

Learned passage scoring uses private numeric development features and actual
source-reviewed labels. Paired collection accepts `--collect-features`; use
`--collect-only --requests 0` for development collection without a latency claim.
`python -m scripts.fit_relevance_scoring --dataset .tmp/scoring/reviewed-development.json --output .tmp/scoring/candidate.json`
requires at least 300 reviewed development cases and writes new candidate and
`.training-report.json` files only under `.tmp/`. Exit codes are `0` candidate
fitted (not accepted), `2` invalid inputs/fit/output failure. No model or database
IO occurs. The paired benchmark accepts `--scoring-candidate` plus
`--scoring-training-report`, checks development/acceptance family separation,
and emits schema-v3 only through existing quality gates. See
[scoring workflow](../docs/relevance-scoring.md).

`python -m scripts.prepare_scoring_review worksheet --dataset .tmp/scoring/development-collection.json --collection .tmp/scoring/collection.json --output-dir .tmp/scoring/review`
creates an escaped HTML worksheet, source JSON and an unapproved review template
in a new private directory. Candidate UUIDs are unioned across arms and ordered
by source identity; scores, feature vectors and ranks are omitted. All labels
start unresolved. After actual source/question/label/family review,
`python -m scripts.prepare_scoring_review export --dataset .tmp/scoring/development-collection.json --collection .tmp/scoring/collection.json --review .tmp/scoring/review/human-review.json --output .tmp/scoring/reviewed-development.json`
requires bound raw hashes, complete boolean union labels and affirmative review
declarations, then exports numeric development rows. It rejects acceptance
splits and never accesses models/DB, infers labels or emits a profile. Both
commands refuse existing outputs; exit codes are `0` prepared/exported (not
retrieval acceptance), `2` invalid/operational failure.

`python -m scripts.promote_relevance_profile --report .tmp/scoring/accepted.json --dataset .tmp/scoring/frozen.json --review .tmp/scoring/human-acceptance.json --profile .tmp/scoring/accepted.semantic.profile.json --semantic-artifact artifacts/semantics/v1/lexicon.json --training-report .tmp/scoring/candidate.training-report.json --version semantic-v1-score-v3-001`
requires a full passing paired report, exact emitted profile and bound actual
human review. It checks model/lexicon inventory and recomputes report summaries
and comparison gates, then copies profile bytes plus whitelisted provenance to
a new immutable version and adds both entries to the inventory. It does not
load a model, access PostgreSQL, edit `.env` or restart services. Exit codes:
`0` promotion verified, `1` rejected or partial promotion requiring reconciliation,
`2` invalid/operational input failure. Do not retry a surviving promotion lock or
partially written version automatically. See
[promotion and recovery](../docs/relevance-scoring.md#independent-evaluation-and-release).

Prepare baseline manifest/cases with `python -m src.cli.prepare_rerank` before
using the benchmark below; see [input preparation](../docs/online-reranking.md#prepare-baseline-calibration-inputs).
New baseline reports include authored-input/package bindings and explicit
reproduction arguments for review/freeze; existing diagnostic report fields and
quality gates remain unchanged. Older collections need recollection to use freeze.

All commands below require explicitly provisioned local dependencies/models for the selected operation. They read publication data where indicated and never publish packages or update vectors. Reports do not by themselves prove source accuracy or production acceptance.

| Command and main arguments | Inputs, outputs and effects | Exit codes |
| --- | --- | --- |
| `python -m scripts.evaluate_retrieval --package chunks/<document_id> --cases .tmp/evaluation/cases.json --model-path <local-model> --model-revision <40-hex> [--top-k 5] [--output .tmp/evaluation/report.json]` | Reads pinned package/cases, local model and PostgreSQL. Writes metadata-only report if output is supplied, otherwise prints it. Top-K range 1-20. Shared reranking flags are available through `--help`. | `0` target met, `1` target missed, `2` failure/usage error. |
| `python -m scripts.benchmark_online_retrieval --manifest .tmp/evaluation/manifest.json --model-path <local-model> --model-revision <40-hex> --output .tmp/evaluation/benchmark.json --rerank [--collect-only] [--requests 100]` | Reads manifest/package/cases/model/DB. Shared reranking flags configure local ONNX. Writes report under `.tmp/`; emits sibling `.profile.json` only when rollout gates pass. `--collect-only` gathers candidate identities for labeling and never emits a profile. | `0` rollout ready, `1` gates not ready (including collection), `2` failure/usage error. |
| `python -m scripts.benchmark_semantic_retrieval --dataset .tmp/semantics/evaluation.json --model-path <local-model> --model-revision <40-hex> --semantic-artifact <reviewed-lexicon> --output .tmp/semantics/paired.json --rerank [--collect-only] [--requests 100] [--intent-artifact <accepted-classifier>]` | Reads all pinned packages/active publications and local models. Compares baseline/semantic/optional intent-assist with cross-document evidence; normal runs independently calibrate every arm and require at least 300 reviewed cases and exact union candidate labels. Refuses `--rerank-profile`. Writes metadata-only report and gated sibling `<stem>.<arm>.profile.json`; never changes runtime configuration. Collection permits draft evaluation cases but still needs a reviewed lexicon; it emits no profile. | `0` collection completed or semantic promotion ready, `1` full gates/comparison failed, `2` invalid inputs/operational failure. |
| `python -m scripts.diagnose_retrieval --manifest .tmp/evaluation/manifest.json --collection .tmp/evaluation/benchmark.json --output .tmp/evaluation/new-diagnostic [--reference .tmp/evaluation/historical-report.json]` | Reads saved collection, pinned cases and current local package sources. Writes `diagnostic.json` and private `source-review.json` in a new directory under `.tmp/`; refuses existing destinations. No model inference, database access, label changes or profile output. A reference cutoff is diagnostic only and requires matching model/corpus fingerprints. | `0` diagnostic written (not acceptance), `2` invalid inputs or operational failure. |
| `python -m scripts.benchmark_intent --dataset .tmp/intent/cases.json --candidate .tmp/intent/candidate.json --training-report .tmp/intent/report.json --model-path <local-model> --model-revision <40-hex> --output-prefix .tmp/intent/bench --rerank --rerank-profile .tmp/evaluation/profile.json [--requests 200]` | Requires reviewed data, candidate/report identity and calibrated retrieval. Reads DB; requires at least 200 requests. Writes `-benchmark.json` and gated `-accepted.json` outputs. Does not change runtime configuration. | `0` accepted, `1` rejected, `2` failure/usage error. |
| `python -m scripts.train_intent --dataset .tmp/intent/cases.json --model-path <local-model> --model-revision <40-hex> --output-prefix .tmp/intent/run [--rerank-preprocessing]` | No DB. Uses shared dataset/training code and writes new `-report.json` / `-candidate.json` artifacts. Candidate is not runtime acceptance. | `0` quality gates pass, `1` quality gates fail, `2` failure/usage error. |

Evaluation/benchmark readers sharing `scripts.evaluate_retrieval.database_state` compare current revision metadata/access/relations, canonical runtime-content/model identity and actual stored chunks/vectors/evidence. Original publication byte hashes may differ after formatting or explicit metadata-only updates; legacy and version-1 fingerprints remain readable without rewriting historical run configuration. Package/model bytes must still remain unchanged during each evaluation. See [publication compatibility](../docs/offline-indexing.md#publication-and-consistency).

See [retrieval cases and reporting limitations](../docs/retrieval-evaluation.md), [calibration and rollout gates](../docs/online-reranking.md), and [intent preparation/acceptance](../docs/intent-classifier.md). Candidate diagnostics are offline-only. The explicit HTTP unassessed mode does not authorize answer generation; the CLI and answer study retain calibrated filtering.

For the answer experiment use `python -m scripts.evaluate_answers <preflight/run/assess> ...`:

- `preflight --cases .tmp/answer-study/cases.json --output .tmp/answer-study/preflight-01` checks configuration/cases and installed native binding identity and optional model hash, writes a new `preflight.json`, but does not start models or establish DB/corpus validity.
- `run` additionally supplies `--model <local-gguf> --model-sha256 <sha256>` and requires the optional pinned `requirements-answer.txt` binding. There are no executable/port arguments. It requires configured filtered retrieval/model/DB and frozen reviewed cases. Loads GGUF in the current Python process on one dedicated thread, writes private samples/report/review template under a new `.tmp/` directory and closes owned resources. A successful run still awaits manual review.
- `assess --report .tmp/answer-study/run-01/report.json --review .tmp/answer-study/review.json --output .tmp/answer-study/decision.json` reads matching report/sample/review identities and writes a new decision file; it does not run models or enable an API.

Exit `0` means static checks passed, experiment completed awaiting review, or assessment accepted, depending on the subcommand. Exit `1` means blocked preflight or assessment rejection; `2` means invalid inputs/experiment failure. See [answer evaluation](../docs/answer-evaluation.md) for prerequisites and evidence gates. Support modules have no CLI and must not be presented as application capabilities.

## Package conversion and task authoring

Preview before applying. See [legacy conversion and layout](../docs/offline-indexing.md) and [structured evidence](../docs/structured-evidence.md).

| Command | Requirements and effects | Exit codes |
| --- | --- | --- |
| `python scripts/convert_chunk_package.py --package <selected-legacy-package> [--apply] [--json]` | Reads one explicitly selected legacy review/approved package and source identity. Default previews; apply creates a current v2 package, preserving originals. No DB/model. | `0` success/skip, `1` rejection, `2` operational/usage failure. |
| `python -m scripts.convert_structured_package --package chunks/<document_id> [--apply]` | Reads valid v2 package. Default previews; apply replaces JSON with a v3 scaffold using locks/backups. Does not infer semantic items/scopes. No DB/model. | `0` success/already v3, `1` operation failure, `2` usage error. |
| `python scripts/migrate_chunk_layout.py --document chunks/<document_id> [--revision-id <uuid>] [--apply] [--json]` | Reads old revision directories; multiple revisions require explicit selection. Default previews; apply migrates selected bytes with recovery backups. No DB/model. | `0` success/skip, `1` rejection, `2` operational/usage failure. |
| `python -m scripts.materialize_noi_quy_packages .tmp/noi-quy-indexing/authored.json` | Requires task-specific `.tmp/noi-quy-indexing/inventory.json`, authored JSON and initialized package. Writes package metadata/extraction/chunks and task cases/coverage under `.tmp/noi-quy-indexing/`. Does not read PDFs, OCR or choose semantic boundaries. No DB/model. | `0` completion; exceptions/validation assertions propagate as nonzero (`1` normally); argparse errors `2`. |

The materializer is tied to that authoring task and is not a general package preparation replacement. It validates using shared package rules; run normal package validation afterward. Do not run it as incidental cleanup.

## Historical assets and retired commands

These asset tools deliberately retain `app/` paths. They do not operate on the current root `assets/` layout without a separately reviewed change. They are historical compatibility tools, not current setup steps. Preserve archived manifests/journals and source bytes.

- `powershell -NoProfile -File scripts/normalize-assets.ps1` previews historical `app/assets` filename normalization. `-Apply` renames and records `app/assets-rename-manifest.json` / `app/assets-rename-journal.json`; `-Rollback` restores recorded names. No DB/model. Successful script completion exits normally; terminating errors fail the process (normally `1` under `-File`). No `--help`; inspect the script and linked historical tests instead.
- `python scripts/restructure_assets.py [preview/apply/rollback] [--run-id <uuid>]` defaults to preview. Requires the historical `app/assets` and catalog layout; apply/rollback move assets, update catalog paths and write journals under `app/data/reports/asset-restructure`. Rollback requires a run ID. No DB/model. Exit `0` success, `2` failure/usage error.
- `python -m scripts.migrate_indexing_chunks` and `python -m scripts.migrate_indexing_workspace` only print retirement guidance and exit `2`. They perform no migration or output-file writes; use explicit selected-package conversion or current preparation as directed.

See [historical test boundaries](../README.md#testing). Do not execute migrations merely to verify this documentation.
