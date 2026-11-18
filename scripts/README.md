# Manual utility and offline experiment reference

This folder contains task-specific tools, offline evaluations, compatibility aliases and supporting modules. Supported application operations live in [src/cli](../src/cli/README.md). Tools stay here because of their role, not because they use Python or can run directly.

Run `python -m scripts.<name>` from the repository root with the repository virtual environment unless a direct-file invocation is explicitly listed. Angle-bracket arguments below are placeholders. Install only the optional dependencies required by the linked guide. No tool implicitly downloads models. Output under `.tmp/` is private local work, not acceptance evidence by itself.

## Inventory

Intent/scoring training kits and their application preparation commands are retired.
Use [catalog tasks](../docs/catalog-answering.md); private experiments remain intact.

| File | Status / role | Purpose |
| --- | --- | --- |
| `try_retrieval.py` | Current manual utility | Run the public retrieval contract from an IDE and display Vietnamese source excerpts/timings. |
| `benchmark_template_answers.py` | Current read-only exposed regression | Run private Type A answer cases without a listener, model generation or acceptance claim. |
| `verify_runtime_assets.py` | Current read-only release utility | Verify selected local model/artifact bytes, hydrated LFS files, the E5 tree and tracked Git paths against the runtime inventory. Permits exact declared authoring sources separately, rejecting undeclared source files; run `python -m src.cli.build_artifacts check` first for full source/output validation. See [source contract](../docs/runtime-artifacts.md#split-source-authoring). |
| `evaluate_retrieval.py` | Current offline evaluator | Evaluate one published package against pinned source-reviewed cases. |
| `evaluate_answers.py` | Retired entrypoint | Returns 2 with template-tool guidance; cannot issue acceptance. |
| `answer_evaluation_contract.py` | Retired support module | Historical placeholder; no study contracts or acceptance gates. |
| `answer_evaluation_runtime.py` | Retired support module | No runtime exports or standalone command. |
| `convert_chunk_package.py` | Supported legacy conversion | Explicit non-destructive v1 review/approved to v2 conversion. |
| `convert_structured_package.py` | Current opt-in conversion | Convert v2 to a v3 scaffold; semantic authoring still required. |
| `migrate_chunk_layout.py` | Supported legacy conversion | Select an old revision directory and migrate to the current package layout. |
| `materialize_noi_quy_packages.py` | Task-specific authoring utility | Serialize explicitly authored labor-rules segments, chunks and local evaluation inputs. |
| `normalize-assets.ps1` | Historical migration | Normalize asset filenames in the historical `app/` layout. |
| `restructure_assets.py` | Historical migration | Apply a fixed asset-folder mapping in the historical `app/` layout. |
| `migrate_indexing_chunks.py`, `migrate_indexing_workspace.py` | Retired entrypoints | Return replacement guidance, without performing migrations. |

## Manual retrieval

For asset verification before retrieval, install Git LFS, run `git lfs pull`, then `python -m scripts.verify_runtime_assets`. Optional `--manifest <repository-relative-path>` selects another inventory. The utility uses only standard-library Python and local Git, without reading `.env`, loading models, accessing a database, downloading or writing files. Use `python -B` to also suppress Python's normal ignored bytecode-cache writes. Exit codes: `0` integrity/policy passed, `1` asset mismatch, `2` invalid manifest or operational failure. JSON output contains paths, fixed error codes and counts. This is not quality acceptance or live database/remote LFS verification. See [checkout and promotion](../docs/runtime-artifacts.md#git-lfs-checkout-and-verification).

Edit `QUESTION`, `TOP_K`, `MODEL_PATH`, `MODEL_REVISION` in `try_retrieval.py`, keeping UTF-8 encoding, then run `python -m scripts.try_retrieval` or execute the file in the IDE. Direct execution works from other working directories without changing them. Its configured local model directory must actually be provisioned.

Requires PostgreSQL, compatible published vectors and a local model. Reads public filtered results through `src.retrieval.runner`, shared with [the retrieval CLI](../src/cli/README.md#retrieval-and-services); does not implement another search algorithm or start HTTP. Output includes full accepted source excerpts and completed timings, without publication writes. Exit codes follow the runner (`0` valid results/empty outcomes, `1` selected input/model failures, `2` operational failure). The compact no-match display does not distinguish every fallback reason; use CLI JSON/HTTP for that. The editable default configuration does not enable calibrated reranking. See [retrieval evaluation](../docs/retrieval-evaluation.md).

## Evaluation and experiments

`python -m scripts.evaluate_retrieval --help` describes package-pinned readonly
retrieval evaluation using unassessed candidates. It checks publication/source
compatibility and expected evidence ranks, not answer completeness. Real cases
and metadata-only reports stay under `.tmp`; no DB data is written.

`python -m scripts.benchmark_template_answers --help` describes an exposed
regression through the real template service without a listener. Private case
JSON and response files stay under `.tmp`. It records outcomes/citations/timings
and artifact identity, never declares acceptance or modifies source/publication.

Intent training, learned scoring, calibration, paired profile promotion and their
Colab kits have been removed. Use the [catalog workflow](../docs/catalog-answering.md)
and `python -m src.cli.build_artifacts impact` for dependency-local source updates.
Existing private experiment files and installed environments are untouched.

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
