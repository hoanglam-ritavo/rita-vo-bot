# Local asset catalog

The catalog prepares source management for local ingestion and future RAG retrieval. It does not parse
documents, generate embeddings, connect to PostgreSQL, or establish policy validity.
All catalog operations described here run locally using Python's standard library.
The separate [database importer](catalog-database.md) synchronizes this metadata
to PostgreSQL without changing this catalog CLI or creating extraction output.

## Commands and storage

From the repository environment:

```powershell
python -m app.cli.asset_catalog init
python -m app.cli.asset_catalog scan
python -m app.cli.asset_catalog validate
```

Paths are anchored to the repository, independent of the current working directory.
`init` inventories every regular file under `app/assets`, except case-insensitive
`Thumbs.db`, and creates `indexing_workspace/catalog/documents.json`. It refuses to overwrite an
existing catalog. Links, Windows junctions/reparse points, non-regular entries and
non-portable source paths are rejected. PDF, DOC, DOCX, XLSX, CSV, Markdown and TXT are recognized
inventory formats, not a promise of parser support. Other extensions require review.

`scan` and `validate` both validate the catalog and compare it with the current
inventory. They never update catalog records. `scan` is the routine drift check;
`validate` is the check after manual metadata edits. Both report added, missing,
changed and duplicate files and possible moves. A move is only a same-hash proposal;
multiple candidates can exist. A changed size or checksum is a discrepancy even when
the path stays the same. Files must not be concurrently edited during a scan.

Each invocation that completes produces a UUID-named JSON report and a readable TXT
report in `indexing_workspace/reports`. Console output contains counts and status only. Reports may
contain private filenames: keep them local. Operational failure prints a generic
error and returns 2; no successful result is claimed. If report publication fails,
an already-published catalog or individual report can remain; inspect the outputs
before retrying. Each file is published atomically, not as a multi-file transaction.
Initial creation uses a same-directory hard link to prevent a concurrent overwrite;
the local filesystem must support hard links. Existing catalogs are never replaced
by CLI operations. Manual updates should use an editor with atomic saves or the
`atomic_write` helper after creating a backup.

| Exit code | Meaning |
| --- | --- |
| 0 | Valid catalog, no actionable filesystem discrepancies |
| 1 | Validation errors, added/missing/changed files, unresolved duplicates or unrecognized formats |
| 2 | Execution failure, invalid JSON, unreadable source, unsafe filesystem entry or existing catalog during init |

`hold` alone is valid and does not cause exit 1. Duplicate groups remain actionable
until every member is reviewed and has an explicit include/exclude decision. They
remain visible even after resolution. Identical bytes do not imply identical access
rights; reviewing duplicates must not merge permissions. Unknown formats continue
to be flagged because this version defines no format approval registry.

## Schema version 1

The top-level object contains exactly `schema_version: 1` and `documents: []`.
Each record contains exactly the fields in the example below. Unknown fields are
rejected to catch misspelled metadata. All IDs are canonical lowercase UUID strings.
The catalog is a current-source inventory: each document ID, revision ID and source
path is unique (paths are compared case-insensitively). A logical document currently
has one tracked source revision. Independently retained copies receive separate
IDs; use relations to describe them. Revision history is retained through secured
catalog backups, not multiple revisions sharing a document ID in this v1 schema.

- `source_path` is relative to `app/assets`, with `/`, without drive, `..`, empty
  components, backslashes, Windows stream syntax or reserved filename characters.
- `sha256` is a lowercase 64-digit hexadecimal checksum; `size_bytes` is a nonnegative
  integer; `extension` is the lowercase suffix including its dot (or empty).
- `title`, `category`, `document_type`, `owner`, `applicability`, `version_label`
  are nonempty strings or null. Initial title and category are filename-derived
  hints only. `document_type` is free text, for example `policy`, `form`, `guide`
  or `operational_snapshot`; classification requires review.
- Effective dates are null or ISO `YYYY-MM-DD`; the start cannot exceed the end.
  Dates are not inferred from filenames or folder names.
- `lifecycle_status`: `unknown`, `active`, `superseded`, `withdrawn`, `expired`.
- `review_status`: `pending`, `reviewed`; `indexing_decision`: `hold`, `include`, `exclude`.
- `access_groups` and `review_notes` are arrays of nonempty strings. An empty access
  list means unspecified access, never public access. Group names must eventually
  be mapped to the backend's authorization system; this CLI cannot verify membership.
- `relations` contains objects with `type` and target `revision_id`. Types are
  `duplicate_of`, `supplements`, `supersedes`, `form_for`. Targets must exist and
  cannot reference themselves. Duplicate relations require matching hashes;
  business relations require reviewed status and evidence notes. Supersedes cycles
  are rejected. Relations are asserted metadata, never guessed from filenames.

`include` requires `reviewed`, `active`, an owner, applicability, access groups and
evidence notes describing the basis for applicability. Known effective dates must
include the local execution date. Unknown dates may remain null if the reviewer
records the basis in notes. Validation checks completeness and consistency, not the
truth of those assertions. It does not implement runtime authorization or retrieval.
`include_count` is the declared selection count, not a claim of ready ingestion when
the report has discrepancies. Hold and reviewed counts are reported separately.

Synthetic example (the checksum is illustrative; it must match actual bytes when
validating a real source):

```json
{
  "schema_version": 1,
  "documents": [{
    "document_id": "00000000-0000-4000-8000-000000000001",
    "revision_id": "00000000-0000-4000-8000-000000000002",
    "source_path": "synthetic/policy.pdf",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "size_bytes": 0,
    "extension": ".pdf",
    "title": "Synthetic policy",
    "category": "synthetic",
    "document_type": null,
    "owner": null,
    "applicability": null,
    "effective_from": null,
    "effective_to": null,
    "version_label": null,
    "lifecycle_status": "unknown",
    "access_groups": [],
    "review_status": "pending",
    "review_notes": [],
    "indexing_decision": "hold",
    "relations": []
  }]
}
```

## Maintenance workflow

Back up the catalog to approved private storage before editing. It contains curated
work and UUIDs that cannot be recovered by rescanning sources. Git ignores this file;
do not treat `indexing_workspace/catalog` like disposable cache or force-add it to Git.

1. Run `scan` and inspect the local report. Review source content and obtain owner
   confirmation for policy applicability, relations and access rights.
2. For a new file, add a record with fresh document/revision UUIDs and the fingerprint
   from a local inventory (`app.catalog.inventory.inventory` and `app.catalog.validation.new_record` are available
   to manual utilities). Keep unknown metadata null and indexing on hold. Do not
   rerun init or regenerate the whole catalog to incorporate one file.
3. For a confirmed move, edit only the source path and extension if applicable;
   preserve both IDs and existing evidence. Do not accept an ambiguous move by hash alone.
4. For changed content, back up the previous record, preserve the logical document ID,
   allocate a fresh revision UUID, and update fingerprint fields. Reset review to
   pending and indexing to hold; clear dates, applicability, lifecycle and relations
   that need reconfirmation. Re-review incoming relations to the old revision rather
   than silently redirecting them. Retain old revision information in private backups.
5. For retirement, retain the original source and record, set lifecycle to withdrawn,
   superseded or expired, choose exclude, and record the reason. If a source must be
   removed by a separate authorized task, back up and remove its current inventory
   record and reconcile incoming relations; otherwise it remains a missing-file error.
6. Resolve duplicate indexing choices without deleting copies or broadening access.
   Then run `validate`. A reviewer must explicitly leave uncertain items on hold.

This workflow does not modify source assets or historical rename records. Current
operational exports must not be treated as authoritative live personnel information.
Future Type B answers still require the authoritative service integration.

## Source synchronization after manual changes

The November 11, 2026 local synchronization reflects user-confirmed source changes:
40 unchanged files moved to unnumbered top-level directories, and the 17 files in
`03-bieu-mau` were intentionally removed. The current inventory is 31 PDF, 7 XLSX
and 2 DOC files. DOCX remains a recognized catalog format but is no longer present.

| Previous directory | Current directory |
| --- | --- |
| `01-noi-quy-qui-dinh` | `noi-quy-qui-dinh` |
| `04-phone-list` | `phone-list` |
| `05-huong-dan-su-dung` | `huong-dan-su-dung` |

For this synchronization, paths below these directories were preserved and each
destination was checked against the original checksum, size and extension. The
40 retained records changed only `source_path`; UUIDs, categories, access rules
and review decisions were preserved. The 17 removed records were backed up and
removed from the current catalog. No retained relation targeted those records.
Removal from the inventory does not establish policy withdrawal or supersession.

For subsequent manual source changes, follow the maintenance workflow above:
back up the exact catalog, prepare an explicit one-to-one path mapping, check
fingerprints and incoming relations, validate the proposed schema, then atomically
replace the catalog. Stop if the source or catalog changes during reconciliation.
Do not recreate the catalog with `init`, infer moves from duplicate hashes alone,
or change approval/access metadata merely to clear validation warnings.

Local recovery records are stored in `indexing_workspace/archive/pre-reorganization/reports/asset-sync/<run_id>/`:
`documents.before.json` is the exact pre-update catalog; `report.json` records
old/new paths, UUIDs, fingerprints, removed records and completion status;
`files.before.json` records pre-update file hashes for scope verification.
Keep these private records in approved backups. A catalog backup does not archive
source bytes. Restoring it alone against the current tree would restore missing
paths; reconcile the intended source snapshot before any recovery.

Run `scan` and `validate` after synchronization. Added, missing and changed counts
must be zero and the schema must be valid. Existing unresolved duplicate groups
still return exit 1 and require review; do not automatically approve their members.
This update synchronized only the local catalog, not PostgreSQL. See the
[database synchronization notes](catalog-database.md#local-source-update-on-november-11-2026).

Existing JSONL/OCR `source` fields and review manifests describe the paths at
processing time. Preserve them and use the synchronization mapping to locate the
current source. For artifacts predating the earlier folder migration, follow that
migration's mapping first. A path-only change does not require OCR or embeddings
to be regenerated; changed source content requires a separately reviewed run.

## Extraction contract

The [single-PDF publisher](ingestion.md) creates
`indexing_workspace/published_artifacts/<document_id>/<revision_id>/<run_id>/` for each attempt, containing
a source snapshot, JSONL and a manifest recording source checksum and parser/OCR,
chunking and embedding versions/configuration. Database chunks reference their
run/revision and original one-based page range. The separate reviewed-JSON
workflow adds text heading/line context and sheet/cell provenance; see
[offline indexing](offline-indexing.md).
Existing experimental JSONL files remain unchanged and are not automatically
migrated or imported. No extraction or index is created by catalog commands.

## Folder migration

This section documents the earlier numbered layout and its recovery contract.
It is historical guidance, not an instruction to run the utility against the
current unnumbered tree. The script, tests and original run records are unchanged.
Its mapping does not cover the later manual moves or removal of `03-bieu-mau`.
Do not apply or roll back that migration across subsequent changes without first
reconciling the source and catalog snapshots.

The task-specific `app/scripts/restructure_assets.py` utility performs a fixed, reviewed
mapping of 15 source files. It does not infer a new taxonomy, rename filenames,
delete duplicate files or change review decisions. It uses the existing Python
environment and standard library; no database connection is made.

```powershell
python app/scripts/restructure_assets.py
python app/scripts/restructure_assets.py preview
python app/scripts/restructure_assets.py apply
python app/scripts/restructure_assets.py rollback --run-id <run-id-from-apply>
```

Preview is the default and writes nothing. Paths are anchored to this repository.
Exit 0 means a successful preview, verified apply/rollback or already-normalized
no-op; exit 2 means a rejected operation or execution error. Unlike catalog checks,
existing duplicate warnings do not block this migration. Source/catalog drift and
schema errors do block it. Stop other source/catalog editors before running apply
or rollback; concurrent migration invocations are not supported.

| Old directory below assets | New directory below assets |
| --- | --- |
| `05-huong-dan-su-dung/e-office/guidelines/<group>` | `05-huong-dan-su-dung/e-office/<group>` |
| `05-huong-dan-su-dung/it-system/bravo/guideline` | `05-huong-dan-su-dung/bravo` |
| `06-tai-lieu-video-hdsd-workit/1-file-huong-dan` | `05-huong-dan-su-dung/workit` |
| `01-noi-quy-qui-dinh/quy-trinh-thanh-ly-tai-san/rtv-quy-trinh-thanh-ly-tai-san-ccdc-phe-lieu` | `01-noi-quy-qui-dinh/quy-trinh-thanh-ly-tai-san` |

The exact filename allowlist is stored in the utility. Unexpected entries in the
affected source branches require review rather than automatically expanding this
mapping. Preflight includes hidden files, links/junctions, case-insensitive collisions,
existing destinations and catalog fingerprints. File content and Windows Hidden
attributes are preserved. The two hidden DOCX files outside the mapping remain
untouched. Catalog updates change only `source_path`; category remains independent
of the storage folder and no new document/revision IDs are allocated.

Three empty placeholders are removed: `02-chinh-sach-phuc-loi`,
`05-huong-dan-su-dung/device`, and the `quy-dinh-hien-hanh` child of
`01-noi-quy-qui-dinh/noi-quy-lao-dong-quy-dinh-quy-trinh-hien-hanh`.
Only explicitly listed old directories are removed, using empty-directory removal
after their files have moved. There is no recursive deletion or `.gitkeep`.
The remaining top-level numeric prefixes are not renumbered.

The original apply saved a manifest, journal and exact original catalog in its
legacy data directory. Existing records have been relocated to
`indexing_workspace/archive/pre-reorganization/reports/asset-restructure/<run_id>/`.
The historical utility still uses its legacy paths and must not be run against the
new workspace. Keep this ignored directory in approved
private backups: it is the old-to-new path mapping and recovery record. The manifest
records source fingerprints/attributes, identities, and original/created/removed
directories. Moves do not overwrite destinations; each completed move is journaled.
All source fingerprints are checked before the catalog is atomically replaced.
Repeated apply after success creates no additional run.

An ordinary failure attempts rollback automatically. After a process interruption,
use the original run ID with rollback; an unfinished journal blocks a fresh apply.
Rollback reconciles actual file locations and hashes, including a move completed
before its journal update. It restores the original catalog and empty directories.
If source files or catalog metadata were subsequently edited, an old path was
occupied, or unexpected entries appeared, it refuses to overwrite or delete them.
Inspect that run and reconcile conflicting changes before retrying; do not erase
the journal to bypass the check. Never run the old rename rollback across this newer
layout without first resolving the newer migration.

Historical rename manifests/journals, experimental JSONL, earlier reports and catalog
backups are preserved. Their old paths describe their original snapshots; use the
new manifest to trace them to current locations. No extraction outputs are rewritten.

## Reviewed JSON ingestion

The [offline indexing CLIs](offline-indexing.md) prepare supported catalog revisions
without database access, seal reviewed JSON, and publish its embeddings after an
explicit metadata import. Recognition does not imply parser support: DOC/DOCX
remain catalog-only. Extraction may start from hold/pending records; approval and
publication require the full include/reviewed/active eligibility contract.
