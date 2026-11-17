# Current corpus publication

Use the existing package and publication pipeline to load `chunks/<document_id>`
into PostgreSQL/pgvector. This is an explicit offline operation, not API startup
work. It does not enable answering, change running API configuration or establish
accepted relevance calibration. See [database design](database-design.md) for
schema ownership and [offline indexing](offline-indexing.md#scope-2-package-reading-embedding-and-publication)
for publication decisions.

An already running API connected to this database can observe newly eligible
publications on subsequent reads without a restart. Existing corpus-bound
relevance profiles may cease to match; publication does not reauthorize them.

## Preconditions and recovery

- Use the repository virtual environment and installed local E5-small model.
  Match model revision and file fingerprint between publication and retrieval.
- Inspect the intended database, extension, migration revision and all 14 table
  counts. Keep the current `.env` private and unchanged. A separate migration
  role must point at the same intended database.
- Stop concurrent publishers and package/model editing. Capture inventory and
  byte hashes before starting. Readers do not authorize concurrent source edits.
- Back up the database with `pg_dump`, retain a schema-only dump and verify a
  restore on a newly created disposable database before schema changes. Pass
  credentials privately; never put passwords in command arguments or reports.
- Keep `bce4184a7f51` immutable; install additive `c7d92e6a104b` guards explicitly.
  Fresh databases require vector before baseline creation. Retired histories
  need a separate reconciliation, not `stamp`, reset or table replacement.

Private inventories, backups, case questions and detailed execution receipts
belong under ignored `.tmp/`. Summaries may contain counts, model/package hashes,
attempt IDs, fixed codes and timings; do not commit internal source excerpts or
credentials. Preserve the source packages, snapshots and model files.

## Execution sequence

Run from the repository root. Replace placeholders with explicitly selected
paths and the 40-hex local model commit; commands never download a model.

```powershell
python -m alembic current
python -m alembic upgrade head
python -m alembic check
python -m src.cli.prepare_documents validate --package chunks/<document_id> --json
python -m src.cli.preflight_documents --package chunks/<document_id> --model-path <local-model> --model-revision <40-hex-commit> --json
python -m src.cli.publish_documents --all --model-path <local-model> --model-revision <40-hex-commit> --dry-run --json
python -m src.cli.publish_documents --all --model-path <local-model> --model-revision <40-hex-commit> --json
```

Validate and preflight **every** selected package before publication. Preflight
checks complete prefixed token lengths without encoding by default. Optional
`--check-embeddings` creates and discards in-memory vectors, so do not use it on
the entire corpus merely to repeat the real publication encoding. Dry-run checks
database conflicts and planned counts but does not tokenize, encode or write.

Stop before publication if any package is invalid, overlength or metadata-blocked.
On an empty database, whole-corpus dry-run may report `relation_target_missing`
for a package whose target is selected but not yet stored: dry-run does not
simulate earlier writes. Inspect the declared target IDs, dry-run and publish
dependency-free packages first, then dry-run the dependent wave against those
committed targets. Never bypass relation validation or insert placeholders. A
missing target outside the selected corpus remains a blocker.
Do not silently truncate, rechunk, infer permissions or replace unknown metadata.
Preserve legitimate repeated text under each source. Publication normalizes
384-dimensional vectors with batch size 16, records provenance, and commits each
package separately; `--all` is not one corpus-wide transaction. Batch relations
are ordered before publication and external targets are checked in PostgreSQL.

Do not use `--force` for ordinary publication. Unchanged publications skip, and
compatible stored exact-text vectors can be reused. Metadata changes require
explicit `--update-metadata`; they are not implicit in an ordinary reload.
Keep per-package results even when a batch exits nonzero.

## Verification and failure handling

Compare database rows with the captured inventory rather than historical README
counts. For the 2026-11-17 input snapshot, the expected initial publication is
29 documents/current revisions/active successful runs and 530 chunk vectors.
Check non-null finite 384-dimensional vectors, content and source spans, metadata,
v3 evidence projections and completed attempt receipts. The database permits
nullable chunk embeddings, so table presence/count alone is insufficient.

Repeat the unchanged publication command. All packages should skip, with stable
run/chunk IDs and counts. Attempt rows may increase because execution receipts
are independent of stored publications. Changed hashes require a new plan.

Known failures before commit leave prior active publications intact. If commit
acknowledgement is uncertain, inspect the returned attempt ID using the
[attempt command](../src/cli/README.md#publication-attempt-inspection). Confirm
the worker stopped before explicit reconciliation; do not auto-retry, mark old
attempts failed by age, truncate tables or reactivate historical runs.

For real retrieval, author two source-grounded questions per document with
expected source/chunk groups and five outside-corpus probes. Retain those private
inputs before examining rankings. Use `RetrievalService` directly with
`relevance_filter_enabled=False`, explicit local model identity, default vector
search and top-k 5; do not edit `.env` or restart the running API. Record exact
evidence hits, source recall and latency. Each document needs at least one of its
two questions to find expected evidence in top 5; report every miss. Duplicate
sources may share valid evidence, but report exact-source and equivalent-evidence
results separately. Do not tune ranking or relabel misses to pass this gate.

These are exposed regression questions, not holdout/calibration acceptance.
Unfiltered candidates retain unassessed relevance; outside-corpus questions need
not return empty matches. Source excerpts are not verified business answers.
Relevance profiles bound to another corpus are not automatically reauthorized.

## Tests and operational limits

Run non-integration tests, then PostgreSQL integration tests against a dedicated
disposable `TEST_DATABASE_URL` separated from application/migration databases.
Integration tests migrate, downgrade, write and truncate synthetic rows. Test
additive migration round-trips with populated evidence/receipts, fresh baseline
creation with unrelated-table preservation, guard enforcement, v2/v3 publication,
rollback and unchanged/reuse decisions. Never run those fixtures on the target
application database. Missing test configuration is an unverified check.

No API payload/type change, ANN index, external vector service, credential change,
automatic source cleanup or live personnel lookup is needed for this corpus.
Measure retrieval timing rather than inventing a performance SLA. Deploying the
API against the corpus and accepting answer quality remain separate work.

## Execution results - 2026-11-17

The existing database was inspected read-only before execution: 14 application
tables, all empty, baseline `bce4184a7f51`, vector present. A full custom-format
backup and schema SQL were retained privately; the backup restored successfully
to a newly created disposable database. Additive guards were then applied to
the target without rebuilding tables, and Alembic schema comparison passed.

All 29 packages (530 chunks) validated and passed real-model token preflight;
the largest prefixed chunk was 421 tokens. Publication used dependency waves
of 26 and 3 packages, each with a successful database dry-run. Actual publication
took about 232 seconds on this machine, excluding preflight/dry-run. No force,
metadata rewrite or source deduplication was used. Read-only verification checked
all stored content, source locations, metadata, six revision relations, v3
projections and 530 finite normalized 384-dimensional vectors. The final corpus
contains 29 current revisions, 29 active successful runs and 29 metadata audits.

An unchanged ordinary repeat skipped all 29 packages with zero encoded chunks.
Hashes of every non-attempt application's table rows remained identical,
including publication, chunk and evidence IDs. The extra execution receipts
bring the final attempt count to 58, all succeeded; none remain unresolved.

Before ranking, 58 supported cases and five outside-corpus probes were authored
from package evidence and hash-pinned privately. Vector-only unfiltered retrieval
returned the exact expected passage in top 5 for **57/58** supported questions;
exact-text equivalent-evidence scoring was also **57/58** and source recall was
**58/58**. All **29/29 documents** met the at-least-one-evidence-hit gate. Case
`8640ee34-2` missed its expected passage while returning the correct document;
the case was neither relabeled nor used to tune ranking. The missed case concerns
HCNS responsibility for selecting a security-service provider.

Warm-service per-question latency across all 63 cases was approximately median
30 ms, p95 42 ms, maximum 95 ms. These local direct-service measurements exclude
model initialization and network/server concurrency. All outside-corpus probes
returned five candidates with `unassessed` / `relevance_filter_disabled`, which
does not establish answerability or out-of-domain rejection. This regression is
not independent holdout, calibration acceptance or a generated-answer study.

The [current verification status](../README.md#current-verification-status)
records test scope and concurrent-edit limits. Detailed inventories, backup,
preflight, wave receipts, exact cases, timings and unchanged-publication hashes
remain under ignored `.tmp/corpus-publication-20261117/`. No secrets or private
document excerpts are copied here. Package/snapshot/model bytes and `.env`
remained unchanged, API services were not restarted, and existing profiles or
answer reports were not promoted or rewritten. The two generated disposable
test databases were removed after verifying their application tables were empty;
the private backup and restore/test receipts remain available.
