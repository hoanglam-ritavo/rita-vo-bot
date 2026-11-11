# Type A catalog database

The database stores catalog metadata and historical revisions. Import does not
extract text, load models, generate embeddings, populate ingestion runs/chunks,
authorize users, or enable retrieval. The local catalog remains the administrative
source of truth. Back it up separately; PostgreSQL is not an edit interface for it.

## Explicit setup and import

After configuring the application database, apply migrations explicitly:

```powershell
python -m alembic upgrade head
python -m app.cli.import_catalog --dry-run
python -m app.cli.import_catalog
```

Commands resolve `indexing_workspace/catalog/documents.json` and `app/assets/` relative to the
repository, independently of the working directory. Import never runs migrations.
Dry-run needs an already migrated database and uses a read-only, repeatable-read
transaction to report planned additions, updates and retirement from the current
catalog. It does not reserve that state for a later import. Console output contains
counts only; no reports or source files are written.

Schema errors, missing/changed sources and files absent from the catalog block the
entire import. Unresolved duplicate groups and unknown formats produce warnings
but do not prevent metadata import. This intentionally differs from the stricter
catalog `scan`/`validate` exit status. Never edit source files or the catalog during
validation/import. Fingerprinting is a point-in-time check, not a filesystem lock.

Exit codes: **0** successful import/preview (possibly with warnings); **1** rejected
schema, source discrepancy or revision identity conflict; **2** filesystem, JSON,
database or other operational failure. Failure messages omit private payloads.

## Six tables

| Table | Purpose |
| --- | --- |
| `documents` | Stable UUID and creation/update timestamps |
| `document_revisions` | Source fingerprint, path, metadata, review, applicability, effective dates and current-catalog flag |
| `document_access_rules` | Group names per revision; an empty set never means public |
| `document_relations` | Explicit directed revision links: duplicate, supplement, replacement and form |
| `ingestion_runs` | PDF processing attempts, configuration/model provenance, status and active-run selection |
| `document_chunks` | Extracted content, source location, extraction method and nullable 384-dimensional vectors |

Catalog document/revision UUIDs are preserved. Source paths retain the catalog's
relative-to-`app/assets` convention. The importer maintains a Python `casefold()` path
key, unique among current revisions; direct database writers must maintain that
key too. Checksums are not unique and never merge source identities or permissions.
Only one revision per document may be current. Historical revisions can retain
the same source path. Foreign keys prevent deleting referenced records.

All catalog metadata belongs to the revision, including descriptive titles,
review notes and permissions. Notes remain a JSONB array. Group membership and
business truth are not verified by this importer. Database constraints enforce
structural integrity; catalog validation enforces cross-record review rules.

Chunks reference runs; runs reference revisions. Chunk indices are unique per
run. `source_location` is a JSON object reserved for page ranges, section paths or
sheet/cell ranges; PDF publication records one-based `page_start` and `page_end`.
The [reviewed-JSON publisher](offline-indexing.md) additionally stores review chunk
IDs and lists of text/page/sheet spans derived from approved extraction snapshots.
Mixed-source chunks preserve each span's method; the summary method is `ocr` if
any contributing span used OCR. These values use existing JSONB columns and need
no schema migration.
`configuration` records parser/OCR/chunking/embedding versions and settings.
The importer never creates these records. The [PDF producer](ingestion.md)
explicitly records the model name/revision; future retrieval must use compatible
query vectors: equal dimension does not establish equal embedding space. PDF
publication writes all chunks and switches active runs in one transaction.
Only successful runs can be active,
with at most one active run per revision. No vector search index is created yet.

## Synchronization and history

One transaction synchronizes the complete snapshot, serialized against other
importers by a shared transaction-level advisory lock. Every task owns its session;
the context controls commit/rollback. Direct database writers do not participate
in this lock protocol and are not a supported metadata administration path.

- Same UUID and unchanged metadata: no new records or timestamp updates.
- Same revision with changed document identity, checksum or size: reject the
  entire import; use a new revision UUID for changed content.
- Same fingerprint with an explicitly updated path: preserve UUIDs and update path.
- Updated metadata: replace the incoming revision's access/relationship sets;
  create all revision targets before inserting links.
- New revision: preserve historical rows; mark only incoming revisions current.
- Revision absent from the snapshot: set `is_catalog_current=false`; retain its
  metadata, links, permissions and any run/chunk records. Do not infer supersession.

An empty valid snapshot marks all revisions non-current. Removing a source record
still requires reconciling the filesystem/catalog before import. Revisions never
previously imported are not reconstructed from backups automatically. Historical
fingerprints do not archive source bytes: retain approved source backups separately.

Current-catalog status is not policy approval or retrieval eligibility. Future
retrieval must check current status, review/indexing decisions, applicability,
effective dates, permissions and compatible active runs before exposing content.
Reimport does not touch run/chunk state, even when permissions or review change.
Operational snapshots remain Type B evidence limitations, never live personnel data.

## Local source update on November 11, 2026

The [local source synchronization](asset-catalog.md#source-synchronization-after-manual-changes)
updated 40 catalog paths and removed 17 intentionally deleted source records after
backing them up. It did not connect to or synchronize PostgreSQL, run migrations,
or change ingestion runs/chunks. Database contents were not checked in this update.

For a separately requested database synchronization, first validate the current
local catalog and source fingerprints, then use the dry-run/import workflow above
against an explicitly configured, already migrated database. Previously imported
retained revisions keep their UUIDs and receive updated paths; previously imported
absent revisions become non-current without deleting their history. Records never
imported are not recovered from local backups automatically. Review the dry-run
against actual database state rather than assuming it matches the old catalog.

## Validation and rollback

Run unit tests with `python -m pytest -m 'not integration'`. Integration tests use
only a dedicated disposable `TEST_DATABASE_URL`; missing configuration skips them.
The catalog integration tests additionally require empty application tables and
clean up their synthetic rows. They exercise concurrent import, rollback, history,
vector dimensions, constraints and migration/model agreement.

Migration `0002_type_a_catalog` creates the six tables. Downgrading to
`0001_enable_vector` explicitly drops them and their data; back up before any
intentional downgrade. The shared vector extension and unrelated tables remain.
