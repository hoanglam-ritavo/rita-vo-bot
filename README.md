# Rita Bot

Rita Bot is a planned Python chatbot for internal knowledge and operational information:

- **Type A:** relatively stable documents, procedures, policies, and reference data, answered through retrieval-augmented generation (RAG) and exact lookup where appropriate.
- **Type B:** changing personnel and project information, queried from authoritative systems through controlled read-only tools. A future service adapter is intended to use gRPC.

Implemented capabilities are the database foundation (Python 3.14, async SQLAlchemy, psycopg 3, PostgreSQL/pgvector, Alembic, and database tests), a local asset catalog CLI, and six Type A storage tables with transactional catalog metadata import. Local PDF ingestion supports review and transactional publication of one approved catalog revision, including OCR, chunks and embeddings. There is no chatbot/API framework or question-routing implementation. `python -m app.cli.check_db` checks the database; it does not start a chatbot. `python main.py` defaults to the database check; explicit `check-db` and `ingest` commands are also supported.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the target design and [AGENTS.md](AGENTS.md) for contributor instructions.

## Repository map

| Location | Purpose |
| --- | --- |
| `app/cli/` | Database/pgvector check, local catalog operations, metadata import and PDF ingestion |
| `app/asset_catalog.py` | Source inventory, catalog validation and atomic local reports |
| `main.py` | Thin dispatcher for database checks and PDF ingestion |
| `app/ingestion/` | Shared local PDF processing and transactional publication |
| `app/db/` | Configuration, async sessions, runtime helpers, Type A models and migration filtering |
| `migrations/`, `alembic.ini` | Explicit migrations for vector and six Type A storage tables |
| `tests/`, `pytest.ini` | Unit and PostgreSQL integration tests |
| `assets/` | Source documents and other reference assets |
| `data/` | Curated local catalog and generated outputs; only its README is kept in Git |
| `scripts/normalize-assets.ps1`, `scripts/test-normalize-assets.ps1` | Asset-renaming utility and its checks, separate from chatbot ingestion |
| `assets-rename-manifest.json`, `assets-rename-journal.json`, `rename-history/` | Asset-renaming records |
| `requirements.txt`, `requirements-dev.txt`, `.env.example` | Dependencies and example configuration |

Files present in `assets/` do not imply parser support. The current collection contains 40 files: 31 PDF, 7 Excel (`.xlsx`) and 2 Word (`.doc`); no DOCX files remain; supported ingestion formats will be established during Type A implementation. The Bravo ZIP package has been removed, and no archives remain in the current collection. Video files have been removed from the current source tree but remain in Git history. Video formats under `assets/` are ignored by Git to avoid accidental reintroduction.

Generated extraction/OCR outputs, caches, and evaluation reports belong under `data/`, separately from original assets. See [data/README.md](data/README.md) for the reserved locations; review and publish create processing subdirectories as needed. Automated tests belong in `tests/`; task-specific manual utilities belong in `scripts/`.

## Source folder layout

The current source layout contains `noi-quy-qui-dinh`, `phone-list` and
`huong-dan-su-dung`. Usage guides are grouped under
`e-office/<group>`, `bravo` and `workit` within the last directory. Asset-disposal
procedures and forms sit directly in `noi-quy-qui-dinh/quy-trinh-thanh-ly-tai-san`.
The November 11, 2026 source update removed the numeric top-level prefixes and
intentionally removed the 17 files in `03-bieu-mau`. The remaining 40 files retain
their content, document/revision identities and catalog metadata; only catalog
source paths changed. Categories are independent of storage folder names.

Catalog backups and path mappings for this update are kept under ignored
`data/reports/asset-sync/<run_id>/`; database synchronization was not performed.
See the [source synchronization workflow](docs/asset-catalog.md#source-synchronization-after-manual-changes).
The fixed `scripts/restructure_assets.py` utility describes an earlier numbered
layout and must not be applied to the current tree. Its
[historical folder migration workflow](docs/asset-catalog.md#folder-migration)
and the old name-normalization records are retained for provenance and recovery.

## Setup (PowerShell)

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

Fill in `DATABASE_URL` in `.env` before running:

```powershell
python -m alembic upgrade head
python -m app.cli.check_db
```

PostgreSQL must already have the target database and the pgvector extension installed on the server. Installing the Python `pgvector` package does not install the server extension. If the migration account cannot enable it, ask the DBA to run `CREATE EXTENSION IF NOT EXISTS vector;` in the target database before migration.

## Local asset catalog

```powershell
python -m app.cli.asset_catalog init
python -m app.cli.asset_catalog scan
python -m app.cli.asset_catalog validate
```

These standard-library commands do not connect to the database or modify source files.
`init` creates the ignored `data/catalog/documents.json` once; `scan` and `validate`
preserve manual metadata and write JSON/TXT reports under `data/reports`. Exit codes
are 0 for no actionable discrepancy, 1 for validation/discrepancy findings (including
unresolved duplicates), and 2 for execution failure. A successfully created catalog
can therefore accompany exit 1. Pending items default to `hold`; hold alone is not
a validation error. Back up the curated catalog separately from disposable caches.
See the [catalog guide](docs/asset-catalog.md) for schema, synthetic example and
maintenance workflows. Inventory format recognition is not ingestion support.

## Local PDF review and publication

See the [ingestion guide](docs/ingestion.md) for publish prerequisites, failure behavior and examples.
`python main.py ingest review <pdf>` and `python -m app.cli.ingest review <pdf>` expose the existing review options.
Review never connects to PostgreSQL.

### PDF/OCR review

For Type A metadata storage, see the [catalog database guide](docs/catalog-database.md).
After explicit migrations, preview with `python -m app.cli.import_catalog --dry-run`,
then synchronize with `python -m app.cli.import_catalog`. Both require the configured
database; neither creates chunks or embeddings or changes the local catalog.

Install example dependencies separately from the database dependencies:

```powershell
python -m pip install -r requirements-example.txt
New-Item -ItemType Directory -Force data/cache/tessdata | Out-Null
Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/vie.traineddata -OutFile data/cache/tessdata/vie.traineddata
Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata -OutFile data/cache/tessdata/eng.traineddata
```

These are official Tesseract language files. PyMuPDF uses integrated OCR support directly, without `pytesseract` or Poppler. Processing runs locally; the PDF is not uploaded.

First inspect extraction without loading an embedding model:

```powershell
python main.py ingest review ./assets/noi-quy-qui-dinh/noi-quy-lao-dong-quy-dinh-quy-trinh-hien-hanh/1-noi-quy-lao-dong-tap-doan-rita-vo.pdf --tessdata data/cache/tessdata --skip-embeddings --output data/extracted/rita-vo-ocr-chunks.jsonl
```

Remove `--skip-embeddings` to generate normalized vectors with `intfloat/multilingual-e5-small`. The first run may download public model weights from Hugging Face. Set `$env:HF_HOME = "$PWD/data/cache/huggingface"` before running to keep its cache under generated data.

Review defaults to `--ocr-mode auto`: native text first, then full-page OCR only for pages without text. Use `--ocr-mode always` to OCR even an existing incomplete/corrupt text layer. Page rotation metadata is normalized in memory before OCR so text sorting uses upright coordinates; the source PDF is never saved or rewritten. Defaults remain `--ocr-language vie+eng`, `--ocr-dpi 300`, and `--output data/extracted/chunks.jsonl`. Language data resolution uses `--tessdata`, then `TESSDATA_PREFIX`, then PyMuPDF discovery. All requested language files must exist when OCR is needed. Missing data or a page OCR error produces a nonzero exit without replacing output. Successfully OCRed but empty pages are reported and skipped; an entirely empty result exits nonzero. JSONL is staged in the output directory and atomically replaced after successful processing; serialization and replacement failures preserve the existing file.

Review JSONL records contain `chunk_index`, original one-based `page`, `content`, `source` (relative to the working directory), and `extraction_method` (`native` or `ocr`), plus `embedding` unless skipped. Text is normalized to Unicode NFC with horizontal whitespace collapsed within each line; line and paragraph breaks are retained. Chunks contain at most 500 characters, prefer paragraph/line/word boundaries, and overlap by at most 100 characters using complete words. Oversized individual tokens require hard splitting. Chunks never cross pages. This is not automatic clause recognition or correction of missing words, diacritics, or reading order. Tables are not reconstructed. Do not commit generated/private content.

For a reproducible review without embeddings, choose a new review directory and output filename:

```powershell
python main.py ingest review ./assets/noi-quy-qui-dinh/noi-quy-lao-dong-quy-dinh-quy-trinh-hien-hanh/1-noi-quy-lao-dong-tap-doan-rita-vo.pdf --pages 1-3,8,15,26,29 --ocr-mode always --tessdata data/cache/tessdata --skip-embeddings --output data/extracted/ocr-fast-review.jsonl --review-dir data/reports/ocr-review/fast-run
```

`--pages` uses one-based numbers and inclusive ranges, removes duplicates and processes pages in document order. All ranges are validated before OCR. `--review-dir` must be new or empty, and the JSONL output must be outside it. Each selected page produces `.raw.txt` and `.normalized.txt` files; OCR pages additionally produce a PNG rendered with the same settings as the raster passed to integrated OCR. Native pages have no OCR input image. The manifest records the source SHA-256, PyMuPDF version, selected pages, language-file paths and hashes, mode, region, DPI, timings and chunk count. `status: extraction_complete` describes extraction, not approval or successful JSONL publication; check the command exit status. Failed runs may leave partial review artifacts; use a fresh directory when retrying. Console messages do not include extracted document content.

To isolate a region, add `--ocr-region LEFT TOP RIGHT BOTTOM` together with `--ocr-mode always`. Coordinates are fractions in `[0, 1]` of the upright page, with origin at the top left, and must describe a positive-area rectangle. For example, `--ocr-region 0.1 0.1 0.9 0.9` selects the central 80%; it is an illustration, **not** a validated crop for this document. Inspect the saved raster for clipped text. The region is rendered at the chosen DPI into a temporary in-memory PDF at its physical size and OCRed. All selected pages share that region; run cover, body and signing pages separately if they need different bounds. Cropping is explicit: no automatic border/stamp removal, deskew, layout detection or handwriting recognition is added.

For an optional model comparison, manually download the official best weights separately; the script never downloads OCR models:

```powershell
New-Item -ItemType Directory -Force data/cache/tessdata_best | Out-Null
Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/vie.traineddata -OutFile data/cache/tessdata_best/vie.traineddata
Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/eng.traineddata -OutFile data/cache/tessdata_best/eng.traineddata
```

Compare the same pages with `fast + vie+eng`, `best + vie+eng`, then `best + vie`, all at 300 DPI, using distinct output/review paths. Compare inspected body regions separately; test 400 DPI on pages 15 and 26 only after choosing a candidate. Keep human-verified reference transcriptions under `data/reports/ocr-review/`, outside run directories. Evaluate before chunking, normalize Unicode and whitespace identically, and compare exactly the same source region. Measure character errors alongside missing lines, numbers, negations and clause order. Better-looking formatting or passing mock tests is not evidence of improved recognition. No winning configuration or policy-grade accuracy is established without that comparison; defaults remain unchanged. Handwritten dates and stamp/signature regions still require manual verification.

An exploratory local comparison found that `tessdata_best` with `vie` separated an interleaved passage better than the mixed-language runs. That observation is limited to an agent-transcribed excerpt awaiting human verification; neither cropping nor 400 DPI improved that excerpt further. It is a candidate for evaluation, not a validated winning configuration. Generated local comparison artifacts, when present, are under `data/reports/ocr-review/` and are not committed.

The synthetic PDF tests mock OCR and embeddings and skip if PyMuPDF is not installed. They also exercise real in-memory PDF rotation/crop geometry without running recognition. Passing them does not establish real-document OCR quality.

## Database configuration and behavior

`DATABASE_URL` uses `postgresql+psycopg://user:password@host:5432/database`. URL-encode special characters in usernames and passwords. Configure TLS for the server, for example `?sslmode=verify-full&sslrootcert=C%3A%2Fcerts%2Froot.crt`. System environment variables take precedence over `.env`. Do not commit `.env` or credentials.

`MIGRATION_DATABASE_URL` optionally supplies an account with DDL permissions; when absent or empty, migrations use `DATABASE_URL`. Migrations run only through explicit Alembic commands, not at application startup. The current downgrade preserves the shared `vector` extension. Future runtime access should use permissions needed for application tables rather than migration privileges.

From the project root, use `python -m app.cli.check_db` (preferred) or `python main.py` (wrapper). Both exit with 0 when connectivity and the extension check succeed, or 1 for configuration, connection, or extension errors. Both always dispose the engine. Importing either entrypoint does not load database configuration or establish a connection. Connection failures use generic messages to avoid exposing credentials; consult server logs for detailed diagnostics.

## Using the database in an async handler

This example demonstrates the existing database helper; no bot/API handler framework is implemented yet.

```python
from sqlalchemy import text
from app.db import session_scope, close_database

async def handle_event():
    async with session_scope() as session:
        return await session.scalar(text('SELECT 1'))

# In the future bot/API shutdown hook:
# await close_database()
```

Each task must call `session_scope()` separately. The context commits on success, rolls back on failure, and closes the session; do not commit manually inside it. The engine is created lazily and shared within one process/event loop. Dispose it before closing that loop or switching to another. SQL echo is disabled by default.

On Windows, the entrypoint and Alembic use `SelectorEventLoop` through `app.db.runtime.run_async`. When integrating a framework, configure a psycopg-compatible loop at the framework-managed entrypoint. Do not call `run_async()` inside an already running event loop.

## Adding migrations

Future models must inherit `app.db.Base` and be imported into `migrations/env.py` before metadata is configured. Generate a revision:

```powershell
python -m alembic revision --autogenerate -m "description"
```

Review the generated migration before applying it with `python -m alembic upgrade head`. The migration template supports `Vector`. Autogeneration skips reflected tables outside application metadata; intentional removal of an application table requires an explicit, reviewed drop migration.

## Testing

Run unit tests:

```powershell
python -m pytest -m 'not integration'
```

For integration tests, configure `TEST_DATABASE_URL` to a dedicated disposable PostgreSQL database with pgvector installed on its server, then run:

```powershell
python -m pytest -m integration
```

Integration tests migrate that database, create/drop randomly named test tables, and use a temporary table for vector checks. They leave `alembic_version` and the extension in the test database. Never use production. The guard rejects a test URL matching the application or migration host/port/database, but cannot detect different hostname aliases for the same server.

To verify both extension states, run the suite against a fresh test database where vector has not been enabled, then rerun with vector already enabled. Missing `TEST_DATABASE_URL` causes integration tests to be skipped; a skipped run does not verify real PostgreSQL connectivity. Do not substitute SQLite for these checks.

## Roadmap

1. **Implemented:** database foundation and checks; local asset catalog, source consistency checks, Type A metadata import and single-PDF ingestion with atomic database publication.
2. **Planned:** broader format/batch ingestion, Type A retrieval, cited answers, and conversational context. Recognize Type B requests and report that the source is not connected.
3. **Planned:** authenticated, authorized Type B read-only service integration.
4. **Planned:** combined questions using document rules and current operational data.

See [ARCHITECTURE.md](ARCHITECTURE.md) for responsibilities, failure handling, and decisions deferred to implementation planning.
