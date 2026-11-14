# Application command reference

This folder owns supported application entrypoints. [Manual utilities](../../scripts/README.md) own task-specific conversions, offline evaluation and experiments. Being executable does not determine folder ownership; reusable behavior belongs in the relevant `src` module.

Run commands from the repository root with the repository virtual environment. Below, `python` means `env/Scripts/python.exe` (or the activated equivalent). Examples use placeholders; replace them with explicitly provisioned paths. No command downloads models automatically. See [setup and environment](../../README.md#setup-powershell).

## Command inventory

| File | Status | Purpose and invocation |
| --- | --- | --- |
| `check_db.py` | Current | `python -m src.cli.check_db`: check PostgreSQL connectivity and pgvector version. |
| `prepare_documents.py` | Current | `python -m src.cli.prepare_documents <init/validate/revise/recover> ...`: manage current agent-authored packages. |
| `preflight_documents.py` | Current | `python -m src.cli.preflight_documents --package <package> --model-path <local-model> --model-revision <40-hex> [--check-embeddings] [--json]`: local technical checks. |
| `publish_documents.py` | Current | `python -m src.cli.publish_documents --package <package> --model-path <local-model> --model-revision <40-hex> [--dry-run] [--json]`: publish eligible packages. |
| `retrieve_documents.py` | Current | `python -m src.cli.retrieve_documents --question "Synthetic policy question" --model-path <local-model> --model-revision <40-hex> [--top-k 5] [--json]`: retrieve accepted source excerpts. |
| `prepare_intents.py` | Current, preferred batch interface | `python -m src.cli.prepare_intents <validate/preflight/run> --config .tmp/intent/config.json [--json]`. |
| `serve_api.py` | Current | `python -m src.cli.serve_api`: serve local HTTP retrieval using environment configuration. |
| `serve_grpc.py` | Current | `python -m src.cli.serve_grpc`: serve local health-only gRPC using environment configuration. |
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
- `publish_documents` requires PostgreSQL, provisioned migrations, an eligible package and supplied local model. Repeat `--package` to select a batch or use `--all` instead. `--update-metadata` permits supported metadata updates; `--force` bypasses identical-run skipping. Publication writes transactionally to PostgreSQL. `--dry-run` reads DB/model files but does not tokenize, embed or publish.

Package commands emit summaries or `--json` reports: exit `0` for success/skips, `1` for rejected input, `2` for operational failure/retired operations. Argparse usage errors also exit `2`. Package-changing operations may use locks/backups under `.tmp/`; reports go to stdout and technical preflight does not modify package data. Do not equate successful validation or preflight with business approval.

## Retrieval and services

`retrieve_documents` needs PostgreSQL and a compatible published corpus/local model. It returns status, matches, nullable fallback reason and timings as JSON (pretty-printed unless `--json`). `--top-k` defaults to `5`, range `1-20`, and is an upper bound after relevance filtering. Reranking and intent observation flags are described by `--help` and the [reranking](../../docs/online-reranking.md) and [intent](../../docs/intent-classifier.md) guides. No accepted relevance profile means candidates may be withheld with `relevance_unavailable`.

Exit `0` includes valid empty outcomes; model mismatch exits `1`; errors use the service's existing `1`/`2` classification. Retrieval does not write publication data. The CLI keeps `run(args)` as a compatibility wrapper around `src.retrieval.runner`; [the IDE utility](../../scripts/README.md#manual-retrieval) uses that same runner. Both return excerpts, not generated answers.

`serve_api` reads configuration, loads the local model and checks PostgreSQL at startup; it binds HTTP until stopped. Exit `0` requires successful server startup; configuration/startup failure exits `1`. No migrations or publication run at startup. See [HTTP setup](../../README.md#local-retrieval-api-and-swagger).

`serve_grpc` binds the configured loopback port and exposes health only, without model/DB checks. Exit `0` on normal shutdown and `1` on configuration/lifecycle failure. See [gRPC setup](../../README.md#local-grpc-server). Neither service accepts application-specific argparse flags or implements a `--help` command; use the linked configuration guides.

`check_db` loads DB configuration only when executed, reports the extension version or a credential-safe error, always closes the engine, and exits `0`/`1`. It does not migrate or write application tables.

## Intent preparation and compatibility

Use `prepare_intents` for repeatable config-driven batches. `validate` requires no ML runtime; `preflight` requires the local tokenizer/model and checks tokens without encoding; `run` requires reviewed data and offline training dependencies. Preflight/run create new private run artifacts under `.tmp/`; run produces a manifest, training report and candidate, not a runtime-accepted artifact. No PostgreSQL is used. Exit `0` indicates successful completion, `1` validation/preflight/quality rejection, `2` operational failure or unreviewed training data, `130` interruption. See [config, outputs and gates](../../docs/intent-classifier.md#unified-offline-preparation-cli).

The historical `scripts.train_intent` shares the training implementation. `scripts.benchmark_intent` remains a separate acceptance step; neither interface automatically changes `.env` or enables a classifier.

Retired catalog/ingestion commands are kept for compatibility. Follow the document workflow above; do not interpret their old names as available catalog imports, PDF extraction or approval commands.
