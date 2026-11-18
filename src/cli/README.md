# Application command reference

This folder owns supported application entrypoints. [Manual utilities](../../scripts/README.md) own task-specific conversions, offline evaluation and experiments. Being executable does not determine folder ownership; reusable behavior belongs in the relevant `src` module.

Run commands from the repository root with the repository virtual environment. Below, `python` means `env/Scripts/python.exe` (or the activated equivalent). Examples use placeholders; replace them with explicitly provisioned paths. No command downloads models automatically. See [setup and environment](../../README.md#setup-powershell).

## Command inventory

| File | Status | Purpose and invocation |
| --- | --- | --- |
| `check_db.py` | Current | `python -m src.cli.check_db`: check PostgreSQL connectivity and pgvector version. |
| `check_employee_source.py` | Current, opt-in readonly foundation | `python -m src.cli.check_employee_source`: metadata-only HRM connectivity, readonly role/transaction and 64-column projection check; exits 0/1, loads root `.env`, closes its own pool; no employee output, writes or migrations. See [employee source](../../docs/employee-source.md). |
| `prepare_documents.py` | Current | `python -m src.cli.prepare_documents <init/validate/revise/recover> ...`: manage current agent-authored packages. |
| `preflight_documents.py` | Current | `python -m src.cli.preflight_documents --package <package> --model-path <local-model> --model-revision <40-hex> [--check-embeddings] [--json]`: local technical checks. |
| `publish_documents.py` | Current | `python -m src.cli.publish_documents --package <package> --model-path <local-model> --model-revision <40-hex> [--dry-run] [--json]`: publish eligible packages. |
| `publication_attempt.py` | Current | `python -m src.cli.publication_attempt --id <uuid> [--reconcile]`: inspect metadata-only execution state; explicit reconciliation finalizes an existing commit receipt without publication/retry. |
| `retrieve_documents.py` | Current | `python -m src.cli.retrieve_documents --question "Synthetic policy question" --model-path <local-model> --model-revision <40-hex> [--top-k 5] [--json]`: retrieve unassessed source candidates. |
| `prepare_semantics.py` | Current, offline authoring | `python -m src.cli.prepare_semantics <inventory/validate/export> ...`: snapshot all packages, validate declared coverage, export a reviewed lexicon. |
| `check_answer_catalog.py` | Current read-only validator | `python -m src.cli.check_answer_catalog [--catalog <path>] [--lexicon <path>]`: defaults to `artifacts/answers/catalog.json` and `artifacts/semantics/lexicon.json`; validates schema-v2 structure and lexical pairing without version discovery or fallback; does not issue acceptance. |
| `build_artifacts.py` | Current, offline authoring | `python -m src.cli.build_artifacts <impact/refresh/check/build/import>`: check or assemble declared lexicon/catalog sources; import accepts optional `--retrieval <path>` for the third group and requires `--lexicon <path> --catalog <path> --output .tmp/<new-directory>`. Uses current packages for authoring reports/build coverage, no model/DB/env IO; import writes a new private tree, build writes changed runtime JSON only, check and impact are read-only; refresh writes declared sources. Exit 0 success, 1 invalid/stale/IO, 2 argument error. Never updates provenance or acceptance. See [source contract](../../docs/runtime-artifacts.md#split-source-authoring). |
| `serve_api.py` | Current | `python -m src.cli.serve_api`: serve local HTTP retrieval and the separately gated opt-in answer route using environment configuration. |
| `main.py` | Compatibility dispatcher | Root `python main.py [check-db]` calls the DB check; `python main.py ingest` returns retirement guidance. Importing `src.cli.main` does not execute it. |
| `asset_catalog.py`, `import_catalog.py`, `ingest.py`, `review.py` | Retired | `python -m src.cli.<name>` only prints replacement guidance and exits `2`; these do not perform the named operations. |
| `__init__.py` | Package initializer | No standalone command. |

## Document workflow

Use [offline indexing](../../docs/offline-indexing.md) for the authoritative package schema, workflow and publication boundaries.

- `prepare_documents init --source policies/example.txt` initializes a source from `assets/`; `init --all` selects all sources. Initialization writes metadata and unchanged source snapshots under `chunks/`. An agent authors extraction/chunks separately.
- `validate --package chunks/<document_id>` reads and structurally validates a package. It does not prove transcription accuracy or publication eligibility.
- `revise --package chunks/<document_id> --source policies/example.txt --supersedes-current` writes a new revision for changed source bytes.
- `recover --package chunks/<document_id>` explicitly restores package storage after an interrupted replacement. All four subcommands accept `--json`; package-changing operations may use locks/backups under `.tmp/`.
- `preflight_documents` requires a structurally valid package and supplied local model. It checks token lengths; `--check-embeddings` additionally encodes in memory. It does not connect to PostgreSQL or persist vectors/package changes. Metadata blockers are reported separately.
- `publish_documents` requires PostgreSQL, provisioned migrations, an eligible package and supplied local model. Repeat `--package` to select a batch or use `--all` instead. Verified unchanged publication skips even after formatting/author-note changes. `--update-metadata` permits supported metadata-only updates without another run/chunk set. Changed publication creates a full new run, reusing compatible exact-text vectors from the active same-revision or current explicitly superseded same-document run. `--force` creates a run and re-encodes all chunks. Publication writes transactionally to PostgreSQL. `--dry-run` reads DB/model files and reports planned counts but does not tokenize, embed or publish. See [publication decisions, counts and compatibility](../../docs/offline-indexing.md#scope-2-package-reading-embedding-and-publication).

Package commands emit summaries or `--json` reports: exit `0` for success/skips, `1` for rejected input, `2` for operational failure/retired operations. Argparse usage errors also exit `2`. Package-changing operations may use locks/backups under `.tmp/`; reports go to stdout and technical preflight does not modify package data. Do not equate successful validation or preflight with business approval.

## Publication attempt inspection

`python -m src.cli.publication_attempt --id <uuid>` needs the configured database and current schema. Default inspection is read-only; output contains IDs, status, fixed error codes and timestamps, never source content. `--reconcile` explicitly writes completion state only when an outcome/run receipt exists. Confirm the old worker has stopped before reconciliation. Missing receipts remain unresolved; neither command retries publication or declares a running attempt failed by age. Exit codes: `0` inspected/reconciled, `1` missing attempt, `2` unavailable/unresolved or invalid arguments. See [execution and failure semantics](../../docs/database-design.md#failure-and-reconciliation).

Publication requires the baseline and additive guards described in [database design](../../docs/database-design.md). Existing databases at `bce4184a7f51` upgrade without resetting their tables. Real executions return `attempt_id`, including safe failure results after attempt allocation. `--dry-run` creates no attempts/audits; it retains the existing result shape. A `publication_outcome_unknown` result requires inspection, not an automatic retry.

## Retrieval and services

The retrieval CLI accepts explicit E5, reranker and semantic settings, returning
unassessed candidates with complete source provenance. Intent/profile options
are removed. `serve_api` reads the active environment settings; it does not
publish, migrate or restart another process. See [runtime contract](../../docs/catalog-answering.md).

## Catalog evidence and source updates

Run `python -m src.cli.build_artifacts impact` for a readonly dependency report for answer and ranking references.
`refresh` deliberately relinks unique unchanged evidence and refreshes coverage;
`build` regenerates the selected two- or three-group runtime set; `check` compares generated bytes. Changed
meaning/context/revisions require targeted source review, not hash edits.
All operate on local package/artifact files, without model/env/DB access.
See [source-update contract](../../docs/catalog-answering.md#updating-chunks).

## Semantic preparation

These commands use no model or database. Inputs/outputs for authored data must
stay under `.tmp/`; choose a new output path rather than overwriting a review:

```powershell
python -m src.cli.prepare_semantics inventory --output .tmp/semantics/new-inventory.json --json
python -m src.cli.prepare_semantics validate --manifest .tmp/semantics/authored-inventory.json --json
python -m src.cli.prepare_semantics export --manifest .tmp/semantics/reviewed-inventory.json --output .tmp/semantics/reviewed-lexicon.json --json
```

`inventory` reads every package JSON and unchanged source snapshot and writes a
pending draft with source excerpts. Agent authoring/review happens separately;
the command does not infer aliases or certify meaning. `validate` checks byte
identity, references, dispositions and declared coverage; a technically valid
draft can still require review. `export` requires reviewed units and review notes
and writes only runtime language concepts, without source/chunk IDs. It exports schema-v2 language rules without enabling runtime or issuing quality acceptance. `--chunks-root` explicitly overrides
the package root for fixtures/controlled authoring. Exit `0` means the requested
step completed, `1` invalid manifest, `2` review-blocked export, operational or
usage failure. See [review and evaluation](../../docs/vietnamese-semantics.md).

Retired catalog/ingestion commands are kept for compatibility. Follow the document workflow above; do not interpret their old names as available catalog imports, PDF extraction or approval commands.
