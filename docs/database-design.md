# Pre-production database design

The 14-table PostgreSQL schema stores v2/v3 packages, publications and evidence.
Provisioning does not publish documents, certify retrieval quality or authorize a
relevance profile. HTTP contracts and package bytes remain unchanged.

## Initialization and boundaries

The revision chain is `bce4184a7f51` (already provisioned baseline) followed by
`c7d92e6a104b` (additive publication guards). Never rewrite or stamp the applied
baseline to install missing functions/triggers. Stop publication workers, back up
and inspect the intended database, then run `python -m alembic upgrade head`.
The guard revision adds functions/triggers without table replacement or row
backfill; existing IDs, vectors, notes, receipts and timestamps are preserved.

For a fresh database, a provisioning role must first execute
`CREATE EXTENSION IF NOT EXISTS vector`. The baseline uses vector columns before
the guard migration can run. Install pgvector on the server separately; no runtime
download or automatic application-startup migration is provided. The guard
revision also checks extension creation idempotently. Test fixtures provision
this prerequisite only after checking disposable-database URL separation.

Downgrading guards to `bce4184a7f51` removes only their four triggers and three
functions, retaining all tables/data and vector. It disables those protections
and is not an application-data rollback. The immutable baseline downgrade drops
application tables: never use it on the application database. No conversion from
the retired `0001` through `0006` chain, reset or automatic corpus transfer is supplied.
Unrelated tables remain outside application ownership. Keep model/package/env
bytes and accepted evaluation artifacts separate from schema work.

See [corpus publication](corpus-publication.md) for backup, inventory, dry-run,
publication and real retrieval verification.

## Data dictionary

| Table | Responsibility and constraints |
| --- | --- |
| `documents` | Stable document UUID and database-maintained timestamps. |
| `document_revisions` | Source identity and current metadata. Trigger protects ID/document/hash/size/extension. Partial unique indexes protect current document/path. |
| `document_access_rules` | Unique access-group labels per revision; metadata, not authenticated authorization. |
| `document_relations` | Typed source/target revision links, no self-reference. |
| `ingestion_runs` | Committed successful publications only, with required model identity, 384 dimensions, fingerprints and `created_at`. At most one active run per revision. |
| `document_chunks` | Run-owned content, source spans and optional vector. V2 author ID/role are both null; v3 has a validated author ID and allowlisted role. |
| `evidence_scopes` | Run/author-keyed authored scope JSON object. |
| `evidence_items` | Run/author-keyed authored item JSON object and same-run parent FK. |
| `evidence_values` | Authored value JSON, same-run item FK and exact Decimal projection for decimal values. |
| `evidence_scope_items` | Ordered unique scope membership; both targets must belong to the same run. |
| `evidence_scope_chunks` | Ordered source-chunk links for each scope, with same-run composite FKs. |
| `evidence_item_chunks` | Ordered source-chunk links for each item, with same-run composite FKs. |
| `publication_attempts` | Execution timing, status, safe error code and transactional outcome/run receipt. Declared revision UUID has no FK because a first publication may not yet have a revision row. |
| `metadata_audits` | Append-only created/updated/superseded events with revision, attempt, timestamp and before/after metadata snapshots. |

All links use restrictive FKs rather than cascading historical deletion.
Attempt/audit timestamps are timezone-aware. Business effective dates use
`Asia/Ho_Chi_Minh` (UTC+07), independently of the host timezone.

## Publication and metadata flow

1. Validate the complete package and local model identity. Invalid inputs at this
   boundary create no execution attempt. Dry-run never creates attempts/audits.
2. Persist a `running` attempt before embedding. The attempt starts before the
   database-aware publication comparison and includes embedding and commit time.
3. Read a repeatable-read snapshot, verify current stored content/projections,
   and select unchanged, metadata-only, reuse or full-encoding behavior.
4. Under the existing global transaction advisory lock, recheck package/model
   bytes and concurrency tokens. Write metadata/audit, chunks/evidence/links,
   active-run changes and the attempt's outcome/run receipt in one transaction.
5. After that transaction commits, finalize the attempt as `succeeded` and set
   its completion time. Unchanged publication writes its receipt in a separate
   transaction without touching metadata or chunks.

Metadata-only updates retain run/chunk IDs and original publication byte hashes.
Audit snapshots include metadata, access groups and resolved relations, excluding
document content, vectors, credentials and operational timestamps. A supersede
also records the old revision's before/after state. Identical metadata produces
no audit row. There is no reviewer, approval state or fabricated actor identity.

Audit UPDATE/DELETE is rejected by a database trigger. The application must own
metadata writes through the publisher to obtain audit coverage; these are not
universal triggers auditing arbitrary privileged SQL. Database owners can bypass
application protections. Test-only cleanup uses explicit TRUNCATE on the guarded
disposable database; normal application code has no history deletion operation.

## Failure and reconciliation

`ingestion_runs` no longer stores execution start/end/error or failed jobs.
Attempts use `running`, `succeeded`, `failed`, `cancelled`. Known failures before
commit and cancellations after safe rollback finalize the attempt with a fixed
error code; document content and exception details are not persisted.

A failure during commit or acknowledgement is **unknown**, not proof of rollback.
The CLI returns `publication_outcome_unknown` and an `attempt_id`. It never
automatically retries. A crash can leave an attempt running indefinitely; elapsed
time alone does not establish failure. Inspect it with:

```powershell
python -m src.cli.publication_attempt --id <attempt-uuid>
```

This command is read-only by default and reports IDs, status, outcome, error code
and timings. After confirming the original worker has stopped, use:

```powershell
python -m src.cli.publication_attempt --id <attempt-uuid> --reconcile
```

Reconciliation can finalize a committed outcome/run receipt; it never publishes,
retries or marks an absent receipt as failed. If the receipt is missing or the
database is unavailable, the attempt remains unresolved and requires investigation.
Exit codes are `0` inspected/reconciled, `1` not found, `2` unavailable/unresolved
or invalid command usage. Cancellation propagates while retaining the attempt ID
for callers; it is not converted into a successful publication response.

## Evidence consistency and retrieval

Authored JSON remains the original representation of content, conditions, source
references and ordered memberships. Relational links and `numeric_value` are
projections, not independently editable alternative sources. Publication writes
them together. Shared storage/evaluation readers compare IDs, parents, exact
Decimals and every membership/position before skip, vector reuse or structured
expansion. Drift raises `evidence_projection_mismatch` rather than silently
selecting one representation. Structured readers join the normalized links.

Database constraints cover simple shapes, types, uniqueness and same-run links.
The package validator still checks parent cycles, source offsets, scope counting
levels and authored semantic rules. Neither layer proves source fidelity or
complete business evidence. The public API still returns ranked excerpts only.

Corpus fingerprint **version 2** includes run/revision/configuration plus current
status, indexing decision, effective dates, sorted access groups, applicability,
source path, title and version label. Attempts, audit events, owner and metadata
timestamps are excluded. Access labels are fetched in one additional batch query,
not per publication. An applicability/access/provenance change can therefore
invalidate calibration without creating a new run. Old fingerprints are not
rewritten or automatically accepted. Recollect/review/calibrate for the target
corpus; retain `relevance_unavailable` without a matching accepted profile.

Exact cosine search, 384-dimensional E5 storage and the global publication lock
remain deliberate defaults. No ANN index, multi-model storage, partitioning,
retention cleanup or automatic aggregate computation is introduced. Measure
query plans and latency on the new disposable corpus before sizing a deployment.

## Acceptance

Run focused schema/publication tests, then `python -m pytest -m 'not integration'`.
PostgreSQL tests require a dedicated disposable `TEST_DATABASE_URL`, distinct from
both application and migration URLs. They verify SQL constraints/triggers,
metadata/audit rollback, normalized links, schema/ORM alignment, concurrent writes,
metadata-bound fingerprints and actual HTTP-to-storage eligibility. Migration-preservation tests round-trip the additive guards with populated
synthetic rows and compare every table. Fresh baseline reconstruction runs only
on an empty disposable application schema.

Missing or unsafe test configuration means PostgreSQL acceptance is unverified.
Mocked tests and offline DDL compilation are not live database, performance,
calibration or release evidence. See the fixed [current verification](../README.md#current-verification-status).
