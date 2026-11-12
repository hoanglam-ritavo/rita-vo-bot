# Rita Bot

Implemented: Python database foundation, local multi-format chunk preparation, reviewed JSON approval, transactional PostgreSQL publication and a local gRPC server with health checking only. Planned: chatbot business RPCs, Type A retrieval, authenticated Type B service tools and routing. There is no functioning chatbot yet.

Source documents remain in `app/assets/`. New processing data lives in ignored `app/chunks/review/` and `app/chunks/approved/`. Each package owns its metadata and provenance; no local catalog, cache, reports, archive or published artifact copy is maintained. Historical `indexing_chunks/` data stays in place and is not scanned or merged automatically. See the [indexing guide](docs/offline-indexing.md) for the agent workflow, commands, schemas and legacy migration.

The primary preparation workflow uses a Codex agent to read the source, retain a provenance-bearing transcription and compose semantic chunks in the existing review package format. Existing loaders/chunkers remain optional. Validation, human review, approval and local-model publication remain separate steps. Agent use is not necessarily offline; the model environment and authorization to process internal content must be established before reading it.

Entrypoints are in `app/cli/`, package processing in `app/ingestion/`, shared metadata validation in `app/catalog/`, database code in `app/database/`, automated tests in `app/tests/`, and manual maintenance utilities in `scripts/`. `main.py` remains a thin database-check dispatcher; its old ingest command returns retirement guidance. Source rename manifests, journals and `rename-history/` remain historical evidence and are not regenerated.

Explicit `prepare_documents approve --content-only` can seal reviewed content under `app/chunks/approved/` with unverified metadata retained. These packages are available for inspection; publication requires a separate approval with complete metadata. See the [content approval workflow](docs/offline-indexing.md#content-approval-with-unverified-metadata).

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

The suite includes real loopback gRPC calls on ephemeral ports, without an external backend or database. On Windows, if the default temporary directory causes cross-drive PDF path errors or long-path failures, use `--basetemp` with a same-drive, extended-length Windows path whose parent directory already exists.

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
