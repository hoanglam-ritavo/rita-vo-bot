# Database schema and code-first migrations

This repository uses SQLAlchemy models to describe the desired PostgreSQL schema and Alembic migrations to apply schema changes explicitly. The workflow is:

`models -> Base.metadata -> Alembic autogenerate -> migration review -> upgrade`

The application does not run migrations at startup. Changing a model alone does not change the database, and `upgrade head` only executes existing migration files; it does not generate migrations from models.

This guide describes the current source configuration, not the verified state of any running database. See the root [README](../../README.md) for environment setup and testing, and [ARCHITECTURE](../../ARCHITECTURE.md) for implemented and planned behavior.

## Configuration and model registration

| Source | Responsibility |
| --- | --- |
| [base.py](base.py) | Defines `Base.metadata` and constraint/index naming conventions. |
| [models/__init__.py](models/__init__.py) | Registers 17 application tables: 14 corpus/publication tables and 3 conversation tables. |
| [migrations/env.py](migrations/env.py) | Imports models, configures Alembic, and runs migrations through the async engine and Windows-compatible runner. |
| [migrations/helpers.py](migrations/helpers.py) | Supplies `target_metadata` and the table-exclusion filter. |
| [config.py](config.py) | Loads the database URL from environment variables or the repository-root `.env`. |
| [alembic.ini](../../alembic.ini) | Locates the migration directory. |

The current head is `d8e031c72b19`, adding 3 conversation tables after the `c7d92e6a104b` guards and applied 14-table `bce4184a7f51` baseline (17 application tables total, excluding Alembic's version table). See [database design](../../docs/database-design.md) for corpus/publication invariants and [conversation storage](../../docs/conversations.md#enablement-and-storage) for chat provisioning. The guard migration preserves existing data; baseline downgrade is destructive and must not run on the application database.

New models must inherit `src.database.Base`, live in `src/database/models/`, and be imported/exported by its `__init__.py`. Otherwise, Alembic cannot compare their tables against the database.

`DATABASE_URL` configures the application connection. A nonempty `MIGRATION_DATABASE_URL` overrides it for migrations, allowing a separate account with DDL permissions; otherwise migrations use `DATABASE_URL`. Environment variables take precedence over `.env`. Both URLs must use the `postgresql+psycopg` driver. Keep credentials out of documentation and version control.

Database creation and server-side pgvector installation are external provisioning prerequisites. Installing the Python package or importing `Vector` does not enable the PostgreSQL extension. Follow [fresh database provisioning](#fresh-database-provisioning) before running the baseline. A provisioning role/DBA needs permission to enable the extension; the migration role needs permissions for the requested DDL, while runtime access should use application permissions rather than provisioning privileges.

URL-encode usernames and passwords in all connection URLs, including `TEST_DATABASE_URL`. For a synthetic password `abc@123`, use `postgresql+psycopg://demo:abc%40123@localhost:5432/demo`; the database password remains unchanged. Keep the separating `@` before the host. Quoting a URL in `.env` does not replace URL-encoding, and a literal `%` must be encoded as `%25`. Shared validation rejects detectable unencoded `@` in the connection authority before connecting, without exposing credentials. It does not automatically repair URLs or establish that credentials are correct; `@` in query values is not treated as a password error.

## Apply existing migrations

Alembic executes pending revisions in dependency order through `down_revision`, not filename order and not just the last script. For a fresh database with no recorded revision, `upgrade head` executes the entire chain below. For an existing database on this chain, it executes only revisions after its recorded state.

### Fresh database provisioning

1. Create the intended empty PostgreSQL database and install pgvector on its PostgreSQL server. Do not use this procedure to convert a populated older database.
2. Before running Alembic, have a provisioning role/DBA connect to that exact database and execute:

   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

   Enabling the extension is required in each target database, even if the migration role has permission to enable it. It is not a separate pgvector connection or a Python driver type-registration step.
3. Activate the virtual environment, configure the intended database URL as described above, and run from the repository root:

   ```powershell
   python -m alembic upgrade head
   ```

The baseline creates `document_chunks.embedding` as `Vector(384)` before the next revision's `CREATE EXTENSION IF NOT EXISTS vector` can execute. That later statement is idempotent but cannot bootstrap a fresh database missing the extension. Without this prerequisite, the baseline fails when creating the vector column, typically with PostgreSQL `type "vector" does not exist`; Alembic stops before the guard revision. The credential-safe wrapper may instead show `DB_SCHEMA_CONFLICT` with SQLSTATE `42704`, without exposing the raw message. Do not infer that every schema-conflict error means pgvector is missing.

The current revision chain is `bce4184a7f51 → c7d92e6a104b → d8e031c72b19`:

| Revision | Purpose |
| --- | --- |
| [bce4184a7f51](migrations/versions/bce4184a7f51_describe_schema_change.py) | Immutable provisioned baseline: 14 tables, constraints and indexes; requires vector first. |
| [c7d92e6a104b](migrations/versions/c7d92e6a104b_publication_guards.py) | Adds timestamp, immutable revision identity and append-only metadata audit functions/triggers without rewriting rows. |
| [d8e031c72b19](migrations/versions/d8e031c72b19_conversations.py) | Adds 3 isolated conversation tables and their indexes; current head. |

### Existing database upgrades

Inspect the intended database's recorded revision and extension prerequisite before upgrading; back it up and stop publication workers and the bot as described in [corpus operations](../../docs/corpus-publication.md) and [conversation provisioning](../../docs/conversations.md#enablement-and-storage). Then run `python -m alembic upgrade head` from the repository root with the intended migration configuration. Existing tables without compatible history require separate reconciliation, not this fresh-install procedure.

Alembic tracks applied revisions in `alembic_version`. Applying the current head again normally has no pending migration to execute; it does not repair arbitrary schema drift or re-run extension creation. Do not rewrite applied revisions, reset, stamp over a mismatch, or downgrade the application database to test installation. Downgrades preserve the shared `vector` extension; conversation downgrade destroys chat data/receipts, and baseline downgrade drops corpus/publication tables. Do not drop the shared extension as cleanup.

## Change models and generate a migration

1. Use a development database managed by this migration history. Before editing models, follow [fresh database provisioning](#fresh-database-provisioning) for a separate empty database, then reach the existing migration head:

   ```powershell
   python -m alembic upgrade head
   ```

2. Edit the ORM models and register any new models in `models/__init__.py`.
3. Generate a candidate migration:

   ```powershell
   python -m alembic revision --autogenerate -m "describe schema change"
   ```

   Autogeneration compares the connected database schema with the currently imported model metadata. It creates a migration file, but does not apply its schema changes.

4. Review the generated `upgrade()` and `downgrade()` before applying them. Check data preservation, constraints, indexes, rename operations, and any required backfill. Keep migrations as explicit schema snapshots; do not import mutable application models into them.
5. Apply the reviewed migration to the development database:

   ```powershell
   python -m alembic upgrade head
   ```

   Commit model changes and the reviewed migration together. Other environments apply that same migration history explicitly; they do not regenerate it independently.

## Inspect migration state and detectable differences

```powershell
python -m alembic current
python -m alembic heads
python -m alembic check
```

| Command | Meaning |
| --- | --- |
| `current` | Reads the revision recorded in the configured database. |
| `heads` | Lists the latest revision endpoint(s) in local migration files. |
| `check` | Checks whether autogeneration would produce pending operations, without creating a migration file. Requires the database to be at the migration head. |

Matching `current` and `heads` establishes migration-version alignment, not complete schema equivalence. A successful `check` only covers differences detectable under the configured comparison rules and filters. These inspection commands do not apply migrations; `current` and `check` connect to the configured database.

## Limits and cases requiring explicit handling

### Migration failure diagnostics

Errors handled inside the Alembic environment produce a credential-safe message on stderr and exit with code 1. Messages identify the observed stage (configuration, engine creation, database connection, Alembic execution or engine cleanup), a stable category and a suggested next check. Existing configuration validation messages are preserved. A validated PostgreSQL SQLSTATE is included when available.

| Category | Meaning and next check |
| --- | --- |
| `DB_AUTH` | Authentication rejected; check migration-role credentials. |
| `DB_CONNECT` | Connection failed; check connectivity, credentials and TLS settings. Without SQLSTATE, the specific cause is not established. |
| `DB_PERMISSION` | Insufficient privileges; review the migration role with the DBA. |
| `DB_UNAVAILABLE` | The configured database does not exist; verify the intended configuration. |
| `DB_SCHEMA_CONFLICT` | A schema object is missing or already exists; compare schema and migration history. |
| `DB_EXECUTION` | Another database operation failed; consult PostgreSQL server logs. |
| `ALEMBIC_NOT_AT_HEAD` | Inspect `current` and `heads`, then review pending migrations before applying them. |
| `ALEMBIC_REVISION` | Revision history cannot be resolved; compare recorded state and available migration files. |
| `ALEMBIC_COMMAND` | Review command arguments and migration state. |
| `MIGRATION_UNKNOWN` | No specific cause was identified; review the migration environment and server logs. |

For example:

```text
[DB_PERMISSION] Alembic execution failed. SQLSTATE=42501.
The database role lacks permission for the requested operation.
Review migration-role privileges with the database administrator.
```

Diagnostics never print raw exception messages, connection URLs, usernames, hosts, SQL, parameters or tracebacks. They do not infer a missing pgvector installation from generic permission or database errors. They report only an observed stage, not an unverified failing revision. If engine cleanup also fails, the primary failure takes precedence; a cleanup-only failure is reported at the cleanup stage. A reported failure does not prove that no prior migration changes were committed.

These diagnostics apply to errors caught inside `migrations/env.py`; CLI errors outside that environment and normal `alembic check` difference results retain Alembic's own behavior. `check_db` messages are unchanged. Migration connections still prefer a nonempty `MIGRATION_DATABASE_URL`, and process environment variables override `.env`. Unit tests simulate diagnostic failures; they do not establish actual PostgreSQL connectivity or permissions.

### Schema comparison limitations

- Autogeneration produces a candidate, not a guaranteed complete migration. Review all generated operations. Table or column renames must be represented as renames where data must be preserved, rather than blindly accepting add/drop operations.
- Adding a required column to populated tables or changing data representation can require a backfill and staged constraint changes. Autogeneration does not design that data migration.
- The current configuration does not enable `compare_server_default`, so changes to model `server_default` values are not automatically compared. Python-side defaults are also distinct from database defaults.
- The table filter skips reflected database tables that have no matching table in application metadata. This protects unrelated tables, but also means deleting a model does not automatically generate a drop for its old table. Intentional table removal requires an explicit, reviewed migration. The filter does not protect columns removed from a still-managed table.
- `Base.metadata.create_all()` is not a replacement for migrations that evolve existing tables. Use the versioned migration workflow for schema updates.

The retired `0001` through `0006` migration chain is not part of the current revision graph. Do not stamp an older database into the new graph; compare schemas and design a separate reconciliation first.

See the [Alembic autogeneration documentation](https://alembic.sqlalchemy.org/en/latest/autogenerate.html) for comparison capabilities and limitations.

## Row timestamps and publication state

Migration `c7d92e6a104b` installs `BEFORE UPDATE` row triggers on `documents` and `document_revisions`. Stop ingestion and explicitly run `python -m alembic upgrade head` before resuming writes. The triggers set `updated_at` to PostgreSQL `statement_timestamp()` for ORM and direct SQL updates, including updates assigning the existing values. An explicit `updated_at` supplied during UPDATE is overwritten. INSERT defaults remain unchanged, and the triggers do not modify `created_at`.

Timestamps describe updates to the row itself, not its children: changing access rules, relations, or a child revision does not automatically touch the parent. ORM operations that emit no UPDATE do not fire a trigger. The model's `server_onupdate=FetchedValue()` describes the server-generated value; it does not install the trigger. Use an explicit `await session.refresh(row, ['updated_at'])` after flush when the updated value is needed immediately.

Existing timestamps are preserved during migration, without backfill: historical edit times cannot be reconstructed. The additive downgrade removes its four triggers and three functions, retaining all stored timestamps, tables and data. Trigger behavior requires PostgreSQL integration tests; offline SQL checks alone do not verify it against a live database.

`DocumentRevision.is_catalog_current` selects the current revision of a document. `IngestionRun.is_active` selects a run within one revision; it does not establish document validity. A superseded revision can retain its active run as history. The implemented public-document retrieval also checks current revision, lifecycle/indexing status, effective dates, public access, successful active runs and compatible model configuration rather than filtering solely by active runs. See the [Scope 2 publication guide](../../docs/offline-indexing.md#scope-2-package-reading-embedding-and-publication) for input mapping and verification boundaries.

The current schema requires model identity, 384 embedding dimensions and publication fingerprints on runs; chunk embeddings remain nullable for storage compatibility. It does not automatically reconcile `chunk_count` with stored chunks. The publication pipeline validates vectors and writes metadata, runs, and chunks in one transaction; other writers must not assume these invariants are all database-enforced. The separate index on `document_chunks.run_id` is retained pending query measurements; no index removal is part of the timestamp migration.

## Existing tables without matching Alembic history

Before applying migrations to a database whose tables were created outside this history, compare its actual schema and recorded revision state with the repository migrations. `upgrade head` does not automatically adopt or merge pre-existing tables and may encounter conflicting objects.

`stamp head` records a revision without executing schema changes. It is not schema synchronization and must not be used to conceal missing migrations or schema differences. Establish a reviewed baseline or reconciliation migration only after inspecting the existing database.

For database verification, follow the root [testing instructions](../../README.md#testing). Integration tests require a dedicated disposable PostgreSQL database through `TEST_DATABASE_URL`; they perform schema and data changes. Do not use the application database for those tests. Documentation review alone does not require migrations, integration tests, or a database connection.
