# Rita Bot

Rita Bot is a planned Python chatbot for internal knowledge and operational information:

- **Type A:** relatively stable documents, procedures, policies, and reference data, answered through retrieval-augmented generation (RAG) and exact lookup where appropriate.
- **Type B:** changing personnel and project information, queried from authoritative systems through controlled read-only tools. A future service adapter is intended to use gRPC.

Currently, only the database foundation is implemented: Python 3.14, async SQLAlchemy, psycopg 3, PostgreSQL/pgvector, Alembic, and database tests. There are no business tables, chatbot/API framework, document parsers, model integrations, or question-routing implementation. `python -m app.cli.check_db` checks the database; it does not start a chatbot. `python index.py` remains supported as a compatibility entrypoint.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the target design and [AGENTS.md](AGENTS.md) for contributor instructions.

## Repository map

| Location | Purpose |
| --- | --- |
| `app/cli/` | Command-line operations; currently the database and pgvector check |
| `index.py` | Thin compatibility wrapper for the database check |
| `app/db/` | Configuration, async sessions, runtime helpers, base metadata, migration filtering |
| `migrations/`, `alembic.ini` | Explicit migrations; currently enables vector only |
| `tests/`, `pytest.ini` | Unit and PostgreSQL integration tests |
| `assets/` | Source documents and other reference assets |
| `data/` | Generated local outputs; only its README is kept in Git |
| `scripts/normalize-assets.ps1`, `scripts/test-normalize-assets.ps1` | Asset-renaming utility and its checks, separate from chatbot ingestion |
| `assets-rename-manifest.json`, `assets-rename-journal.json`, `rename-history/` | Asset-renaming records |
| `requirements.txt`, `requirements-dev.txt`, `.env.example` | Dependencies and example configuration |

Files present in `assets/` do not imply parser support. The current collection contains PDF, Word (`.doc`, `.docx`), and Excel (`.xlsx`) files; supported ingestion formats will be established during Type A implementation. The Bravo ZIP package has been removed, and no archives remain in the current collection. Video files have been removed from the current source tree but remain in Git history. Video formats under `assets/` are ignored by Git to avoid accidental reintroduction.

Generated extraction/OCR outputs, caches, and evaluation reports belong under `data/`, separately from original assets. See [data/README.md](data/README.md) for the reserved locations; no processing subdirectories or pipelines exist yet. Automated tests belong in `tests/`; task-specific manual utilities belong in `scripts/`.

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

## Database configuration and behavior

`DATABASE_URL` uses `postgresql+psycopg://user:password@host:5432/database`. URL-encode special characters in usernames and passwords. Configure TLS for the server, for example `?sslmode=verify-full&sslrootcert=C%3A%2Fcerts%2Froot.crt`. System environment variables take precedence over `.env`. Do not commit `.env` or credentials.

`MIGRATION_DATABASE_URL` optionally supplies an account with DDL permissions; when absent or empty, migrations use `DATABASE_URL`. Migrations run only through explicit Alembic commands, not at application startup. The current downgrade preserves the shared `vector` extension. Future runtime access should use permissions needed for application tables rather than migration privileges.

From the project root, use `python -m app.cli.check_db` (preferred) or `python index.py` (compatible). Both exit with 0 when connectivity and the extension check succeed, or 1 for configuration, connection, or extension errors. Both always dispose the engine. Importing either entrypoint does not load database configuration or establish a connection. Connection failures use generic messages to avoid exposing credentials; consult server logs for detailed diagnostics.

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

1. **Implemented:** database foundation and checks.
2. **Planned:** Type A ingestion, retrieval, cited answers, and conversational context. Recognize Type B requests and report that the source is not connected.
3. **Planned:** authenticated, authorized Type B read-only service integration.
4. **Planned:** combined questions using document rules and current operational data.

See [ARCHITECTURE.md](ARCHITECTURE.md) for responsibilities, failure handling, and decisions deferred to implementation planning.
