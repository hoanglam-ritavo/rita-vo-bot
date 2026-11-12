# Rita Bot

Implemented: Python database foundation, agent-authored multi-format chunk packages, structural validation, transactional PostgreSQL publication and a local gRPC server with health checking only. Planned: chatbot business RPCs, Type A retrieval, authenticated Type B service tools and routing. There is no functioning chatbot yet.

Source documents remain in `app/assets/`. Ignored packages live at `app/chunks/<document_id>/<revision_id>/`, containing `metadata.json`, `extraction.json`, `chunks.json` and an unchanged source snapshot. Agents author transcription and semantic chunks; Python initializes packages, manages revisions, validates, embeds with an explicitly supplied local model and publishes. There is no approval step or reviewer identity. Validation checks consistency, not transcription accuracy or business correctness.

See the [indexing guide](docs/offline-indexing.md) for schemas, commands, metadata requirements and explicit non-destructive conversion of existing review/approved packages. Historical `indexing_chunks/` is untouched and never scanned. Model environments and authorization to process internal content must be established before agent reading; no hosted processing is implied.

Entrypoints are in `app/cli/`, package processing in `app/ingestion/`, metadata validation in `app/catalog/`, database code in `app/database/`, tests in `app/tests/` and manual utilities in `scripts/`. `main.py` remains a thin database-check dispatcher. Source rename manifests, journals and `rename-history/` remain historical evidence.

Migration `0003_agent_packages` removes revision review state and renames review notes to evidence notes, preserving chunks, vectors and successful run history. Stop ingestion and run migrations explicitly before using the new publication code; conversion of local packages is separate and never changes the database.

See [ARCHITECTURE.md](ARCHITECTURE.md) and [AGENTS.md](AGENTS.md).

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

## Local gRPC server

After installing the dependencies, run `python -m app.cli.serve_grpc` from the repository root. This command does not require database configuration, migrations or a model. The intended inbound direction is the monolithic backend calling this chatbot microservice; business RPC contracts are not implemented yet.

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

`MIGRATION_DATABASE_URL` optionally supplies an account with DDL permissions; when absent or empty, migrations use `DATABASE_URL`. Migrations run only through explicit Alembic commands, not at application startup. The current downgrade preserves the shared `vector` extension. Future runtime access should use permissions needed for application tables rather than migration privileges.

For example, the synthetic password `abc@123` must be written as `abc%40123` in a URL: `postgresql+psycopg://demo:abc%40123@localhost:5432/demo`. This changes only the URL representation, not the database password. Keep the separating `@` before the host; quoting the URL does not replace URL-encoding. The same rule applies to migration and test URLs. Configuration validation rejects detectable unencoded `@` in the connection authority with a credential-safe message; it does not automatically repair URLs or verify credentials/connectivity. Encode a literal `%` as `%25` to avoid unintended decoding.

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

The non-integration suite includes real loopback gRPC calls on ephemeral ports, without an external backend or database. Synthetic multi-format package/converter tests check JSON contracts and byte preservation; they do not parse actual PDF/XLSX files or verify agent extraction accuracy. Publication tests are in `app/tests/test_publication.py` and `app/tests/test_publication_integration.py`; the production module retains its existing name.

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
powershell -NoProfile -ExecutionPolicy Bypass -File app/tests/powershell/test-normalize-assets.ps1
```
