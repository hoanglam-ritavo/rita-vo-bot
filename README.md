# Rita Bot

Implemented: Python database foundation, agent-authored multi-format chunk packages, structural validation, transactional PostgreSQL publication, public-document retrieval through a function/CLI and a local gRPC server with health checking only. Planned: answer generation, chatbot business RPCs, authenticated Type B service tools and routing. There is no answer-generating chatbot yet.

Source documents remain in `app/assets/`. Git-trackable current packages live at `app/chunks/<document_id>/`, containing `metadata.json`, `extraction.json`, `chunks.json` and an unchanged source snapshot. Agents author transcription and semantic chunks; Python initializes packages, manages revisions, validates, embeds with an explicitly supplied local model and publishes. There is no approval step or reviewer identity. Validation checks consistency, not transcription accuracy or business correctness.

Each source has at most one current package. Editing chunks keeps the source revision; changed source bytes require `python -m app.cli.prepare_documents revise --package app/chunks/<document_id> --source <relative-source-path> --supersedes-current`. Revision identity stays in metadata while the directory remains stable. Git preserves committed package history; `.gitattributes` preserves exact bytes across checkouts. Each environment still needs its own local model and explicit publication to its own database. See [layout migration and deployment](docs/offline-indexing.md#layout-migration-and-deployment) before using an older checkout's packages.

See the [indexing guide](docs/offline-indexing.md) for schemas, commands, metadata requirements and explicit non-destructive conversion of existing review/approved packages. Historical `indexing_chunks/` is untouched and never scanned. Model environments and authorization to process internal content must be established before agent reading; no hosted processing is implied.

Before publication, use `python -m app.cli.preflight_documents --package <package-path> --model-path <local-model-directory> --model-revision <40-hex-commit> --json`. Install the optional local runtime from `requirements-example.txt` first. Preflight loads the local model and checks every chunk's token length without encoding by default; add `--check-embeddings` to generate and validate vectors in memory. It needs no database, accepts structurally valid hold/unknown packages, and never changes metadata or package files. Publication metadata blockers are reported separately. Passing preflight is not permission to publish or evidence of transcription/retrieval quality. Publication still performs its own checks; its `--dry-run` does not tokenize or embed. See the [preflight details](docs/offline-indexing.md#technical-preflight).

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

## Local question retrieval

Install `requirements-example.txt` for the local embedding runtime. Call the retrieval CLI with an existing local model directory and its declared full commit:

```powershell
python -m app.cli.retrieve_documents --question "What benefits are available?" --model-path C:/models/multilingual-e5-small --model-revision <40-hex-commit> --top-k 5 --json
```

Replace the model placeholders before execution. The command returns source chunks, not an LLM answer. `--top-k` defaults to 5 (range 1–20). Questions must be nonempty strings with at most 8,000 characters after trimming and fit the model token limit including `query: ` and special tokens; no truncation is performed. The local model is loaded without downloads or remote code. Match output includes content, source spans, document/revision/run IDs, title, relative source path, applicability, effective dates and cosine distance. Smaller distances rank first; distances are not confidence scores. There is no relevance threshold, so even unrelated questions may return candidates.

Only current, include/active, effective public revisions with active successful runs are searched. Runtime model name, revision, file fingerprint, normalization, dimension and passage prefix must match the indexed run. Private documents cannot be enabled through a CLI argument. Exact cosine search needs no new vector index. Database transactions are read-only; no package or vector data is modified. Statuses distinguish `matches_found`, `no_eligible_documents`, `model_mismatch` and `no_matches`; operational failures are errors, never empty evidence. Exit codes are 0 for valid results including empty results, 1 for invalid input/model incompatibility and 2 for operational errors. There is no automatic file logging of questions or retrieved text.

To avoid passing Vietnamese text through shell arguments, open `scripts/try_retrieval.py`, save it as UTF-8, and edit `QUESTION`, `TOP_K`, `MODEL_PATH` and `MODEL_REVISION` at the top. Select this repository's `env/Scripts/python.exe` interpreter in your IDE, then run the file. Its default model path points to the locally prepared pinned model under `.tmp/models`; that model directory and PostgreSQL configured in `.env` must still be available. Direct file execution works from any working directory; imports and model paths are anchored to the repository, without changing the working directory. Alternatively, from the repository root run:

```powershell
.\env\Scripts\python.exe -m scripts.try_retrieval
```

The script always prints a compact Vietnamese text view: the question, up to three candidates in retrieval order, each document title and deduplicated source locations, and the complete chunk content. The footer shows displayed/total matches; `TOP_K` still controls retrieval, independently of the fixed display limit. IDs, distances and other technical metadata are hidden. There is no DEBUG mode or additional environment setting. Use `app.cli.retrieve_documents --json` with its normal arguments for detailed JSON. UTF-8 output is configured where supported; incompatible IDE streams receive lossless character escapes in the same text format, rather than replacement question marks. The script cannot restore accents already lost in `QUESTION`. It reuses the CLI runner and shutdown/error handling, preserves exit codes and saves no questions/results to files. Empty results and errors have short Vietnamese messages. These are source candidates, not an LLM answer; presentation changes do not improve retrieval ranking.

The script always ends with two timings: model initialization (including fingerprinting and loading inside `service.start()`) and retrieval after the model is ready (query embedding, PostgreSQL search and result construction inside `service.retrieve()`). Measurements use `time.perf_counter()` and exclude input validation, service construction, shutdown, formatting and terminal output. CLI JSON includes unrounded seconds in `timings.model_initialization_seconds` and `timings.retrieval_seconds`; the script displays seconds to three decimal places. A failed or unstarted step has `null`, displayed as an unavailable completed measurement, not zero. Completed measurements survive later retrieval/shutdown errors. Each script invocation loads the model again, and the first retrieval may include database connection setup and first-execution overhead. These are observations of that invocation, not a stable benchmark or LLM answer-generation latency; no warm-up or extra query is performed.

For repeated calls, reuse a service instance on one event loop:

```python
from app.retrieval import RetrievalService
from app.database import close_database

async def retrieve_example(model_path, model_revision):
    service = RetrievalService(model_path=model_path, model_revision=model_revision)
    try:
        await service.start()
        return await service.retrieve("What benefits are available?", top_k=5)
    finally:
        await service.close()
        await close_database()  # Application owner closes the shared database at shutdown.
```

The application owns the loop; use the existing `run_async()` wrapper only at a synchronous Windows entrypoint, never inside another event loop. Model operations run in a worker thread and are serialized per service. Model files must remain immutable until the service closes; restart with a new service after changing models. Service close does not dispose the shared database. Imports do not load configuration, models or connect. There is no HTTP/RPC chat endpoint, conversation history, personnel lookup or answer generation.

For the locally published benefits package, `python -m scripts.evaluate_retrieval --model-path <local-model-directory> --model-revision <40-hex-commit> --output .tmp/retrieval-evaluation.json` runs a fixed manual evaluation: 12 questions across six topics plus two unsupported questions, always at top-5. It records expected-source hits, distances, timings and before/after database counts without printing source content or changing packages. Exit 1 means the recall target was not met; it is not a database error. The real-model evaluation is separate from synthetic unit/integration tests, and an embedding/publication success alone does not prove retrieval quality.

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
