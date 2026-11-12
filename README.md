# Rita Bot

Rita Bot is a planned Python chatbot for internal knowledge and operational information:

- **Type A:** relatively stable documents, procedures, policies, and reference data, answered through retrieval-augmented generation (RAG) and exact lookup where appropriate.
- **Type B:** changing personnel and project information, queried from authoritative systems through controlled read-only tools. A future service adapter is intended to use gRPC.

Implemented capabilities are the database foundation (Python 3.14, async SQLAlchemy, psycopg 3, PostgreSQL/pgvector, Alembic, and database tests), a local asset catalog CLI, and six Type A storage tables with transactional catalog metadata import. Local ingestion supports PDF, XLSX, CSV, Markdown and TXT preparation, editable JSON review, hash-bound approval and transactional embedding/publication, individually or in sequential batches. The legacy single-PDF workflow remains available. There is no chatbot/API framework or question-routing implementation. `python -m app.cli.check_db` checks the database; it does not start a chatbot. `python main.py` defaults to the database check; explicit `check-db` and `ingest` commands are also supported.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the target design and [AGENTS.md](AGENTS.md) for contributor instructions.

## Repository map

| Location | Purpose |
| --- | --- |
| `app/cli/` | Database/pgvector check, local catalog operations, metadata import and PDF ingestion |
| `app/catalog/` | Source inventory, metadata validation, local reports and database import |
| `main.py` | Thin dispatcher for database checks and PDF ingestion |
| `app/ingestion/` | Shared local PDF processing and transactional publication |
| `app/database/` | Configuration, async sessions, runtime helpers, per-table ORM models and explicit migrations |
| `app/database/migrations/`, `alembic.ini` | Explicit migrations for vector and six Type A storage tables |
| `app/tests/`, `pytest.ini` | Unit and PostgreSQL integration tests |
| `app/assets/` | Source documents and other reference assets |
| `indexing_workspace/` | Curated local catalog and generated outputs; only its README is kept in Git |
| `scripts/` | Task-specific maintenance and historical data-migration utilities; automated checks remain in `app/tests/` |
| `assets-rename-manifest.json`, `assets-rename-journal.json`, `rename-history/` | Asset-renaming records |
| `requirements.txt`, `requirements-dev.txt`, `.env.example` | Dependencies and example configuration |

Files present in `app/assets/` do not imply parser support. The current collection contains 40 files: 31 PDF, 7 Excel (`.xlsx`) and 2 Word (`.doc`); no DOCX files remain; supported ingestion formats will be established during Type A implementation. The Bravo ZIP package has been removed, and no archives remain in the current collection. Video files have been removed from the current source tree but remain in Git history. Video formats under `app/assets/` are ignored by Git to avoid accidental reintroduction.

Generated extraction/OCR outputs, caches, and evaluation reports belong under `indexing_workspace/`, separately from original assets. See [indexing_workspace/README.md](indexing_workspace/README.md) for the reserved locations; review and publish create processing subdirectories as needed. Automated tests belong in `app/tests/`; task-specific manual utilities belong in `scripts/`.

## Indexing workspace

`indexing_workspace/` is a processing workspace, not physical database storage.
The current PDF review output goes to `review/chunks.jsonl`; it is not approved.
New publication artifacts go to `published_artifacts/`. Previous extraction and
report files are preserved under `archive/pre-reorganization/`, with relocation
hashes and mappings. The two document CLIs create review packages and explicit
`approved/` snapshots for embedding; see the [offline indexing guide](docs/offline-indexing.md).
See the [workspace guide](indexing_workspace/README.md) for retention and recovery.

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
`indexing_workspace/archive/pre-reorganization/reports/asset-sync/<run_id>/`; database synchronization was not performed.
See the [source synchronization workflow](docs/asset-catalog.md#source-synchronization-after-manual-changes).
The fixed `scripts/restructure_assets.py` utility describes an earlier numbered
layout and must not be applied to the current tree. Its
[historical folder migration workflow](docs/asset-catalog.md#folder-migration)
and the old name-normalization records are retained for provenance and recovery.
The maintenance scripts are consolidated under `scripts/`; their historical data
locations are unchanged. `restructure_assets.py` still targets `app/assets/` and
`app/data/`; `normalize-assets.ps1` still targets `app/assets/` and the manifest
and journal directly under `app/`, not the records at repository root. These
historical utilities are not a maintenance workflow for the current source tree.

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
`init` creates the ignored `indexing_workspace/catalog/documents.json` once; `scan` and `validate`
preserve manual metadata and write JSON/TXT reports under `indexing_workspace/reports`. Exit codes
are 0 for no actionable discrepancy, 1 for validation/discrepancy findings (including
unresolved duplicates), and 2 for execution failure. A successfully created catalog
can therefore accompany exit 1. Pending items default to `hold`; hold alone is not
a validation error. Back up the curated catalog separately from disposable caches.
See the [catalog guide](docs/asset-catalog.md) for schema, synthetic example and
maintenance workflows. Inventory format recognition is not ingestion support.

## Offline document preparation and publication

```powershell
python -m app.cli.prepare_documents extract --all
python -m app.cli.prepare_documents validate --review <review-directory>
python -m app.cli.prepare_documents approve --review <review-directory> --reviewer "Local reviewer"
python -m app.cli.publish_documents --approved <approval-directory> --model-revision <40-hex-commit> --dry-run
python -m app.cli.publish_documents --approved <approval-directory> --model-revision <40-hex-commit>
```

Edit `draft.json` before validation/approval. Publication embeds the approved bytes;
it never reparses or rechunks the source. Synchronize catalog metadata explicitly
before publishing. Supported inputs: PDF, XLSX, CSV, Markdown and TXT; DOC/DOCX are
not supported by these parsers. Install `requirements-example.txt` for optional
PDF/OCR, XLSX and embedding libraries. See the [complete workflow and JSON contract](docs/offline-indexing.md).

## Legacy local PDF review and publication

See the [ingestion guide](docs/ingestion.md) for publish prerequisites, failure behavior and examples.
`python main.py ingest review <pdf>` and `python -m app.cli.ingest review <pdf>` expose the existing review options.
Review never connects to PostgreSQL.

Install `requirements-example.txt` for local PDF processing, then follow the
[OCR setup and evaluation guide](docs/ingestion.md#pdfocr-review).
For a first review (replace the illustrative path with a real source):

```powershell
python main.py ingest review ./app/assets/synthetic.pdf --skip-embeddings --tessdata indexing_workspace/cache/tessdata
```

## Database configuration and behavior

`DATABASE_URL` uses `postgresql+psycopg://user:password@host:5432/database`. URL-encode special characters in usernames and passwords. Configure TLS for the server, for example `?sslmode=verify-full&sslrootcert=C%3A%2Fcerts%2Froot.crt`. System environment variables take precedence over `.env`. Do not commit `.env` or credentials.

`MIGRATION_DATABASE_URL` optionally supplies an account with DDL permissions; when absent or empty, migrations use `DATABASE_URL`. Migrations run only through explicit Alembic commands, not at application startup. The current downgrade preserves the shared `vector` extension. Future runtime access should use permissions needed for application tables rather than migration privileges.

From the project root, use `python -m app.cli.check_db` (preferred) or `python main.py` (wrapper). Both exit with 0 when connectivity and the extension check succeed, or 1 for configuration, connection, or extension errors. Both always dispose the engine. Importing either entrypoint does not load database configuration or establish a connection. Connection failures use generic messages to avoid exposing credentials; consult server logs for detailed diagnostics.

## Using the database in an async handler

This example demonstrates the existing database helper; no bot/API handler framework is implemented yet.

```python
from sqlalchemy import text
from app.database import session_scope, close_database

async def handle_event():
    async with session_scope() as session:
        return await session.scalar(text('SELECT 1'))

# In the future bot/API shutdown hook:
# await close_database()
```

Each task must call `session_scope()` separately. The context commits on success, rolls back on failure, and closes the session; do not commit manually inside it. The engine is created lazily and shared within one process/event loop. Dispose it before closing that loop or switching to another. SQL echo is disabled by default.

On Windows, the entrypoint and Alembic use `SelectorEventLoop` through `app.database.runtime.run_async`. When integrating a framework, configure a psycopg-compatible loop at the framework-managed entrypoint. Do not call `run_async()` inside an already running event loop.

## Adding migrations

Future models must inherit `app.database.Base`, live in `app/database/models/`, and be exported by its `__init__.py`. Alembic imports this package in `app/database/migrations/env.py` to register all tables before metadata is configured. Generate a revision:

```powershell
python -m alembic revision --autogenerate -m "description"
```

Review the generated migration before applying it with `python -m alembic upgrade head`. The migration template supports `Vector`. Autogeneration skips reflected tables outside application metadata; intentional removal of an application table requires an explicit, reviewed drop migration.

## Testing

Run unit tests (shared database fixtures only execute when requested by integration tests):

```powershell
python -m pytest -m 'not integration'
```

For integration tests, configure `TEST_DATABASE_URL` to a dedicated disposable PostgreSQL database with pgvector installed on its server, then run:

```powershell
python -m pytest -m integration
```

Integration tests migrate that database, create/drop randomly named test tables, and use a temporary table for vector checks. They leave `alembic_version` and the extension in the test database. Never use production. The guard rejects a test URL matching the application or migration host/port/database, but cannot detect different hostname aliases for the same server.

To verify both extension states, run the suite against a fresh test database where vector has not been enabled, then rerun with vector already enabled. Missing `TEST_DATABASE_URL` causes integration tests to be skipped; a skipped run does not verify real PostgreSQL connectivity. Do not substitute SQLite for these checks.

The PowerShell utility checks use synthetic temporary sources:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File app/tests/powershell/test-normalize-assets.ps1
```

## Roadmap

1. **Implemented:** database foundation and checks; local asset catalog, source consistency checks, Type A metadata import, multi-format reviewed-JSON ingestion and legacy single-PDF ingestion with atomic database publication.
2. **Planned:** Type A retrieval, cited answers, and conversational context. Recognize Type B requests and report that the source is not connected.
3. **Planned:** authenticated, authorized Type B read-only service integration.
4. **Planned:** combined questions using document rules and current operational data.

See [ARCHITECTURE.md](ARCHITECTURE.md) for responsibilities, failure handling, and decisions deferred to implementation planning.
