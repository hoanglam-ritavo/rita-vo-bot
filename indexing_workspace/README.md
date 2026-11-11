# Indexing workspace

This is a local document-processing workspace, not PostgreSQL/MongoDB physical
storage or mock application data. Source documents remain in `app/assets/`.
All paths below are relative to this directory. Only this README is tracked in Git.

| Location | Meaning | Retention |
| --- | --- | --- |
| `catalog/documents.json` | Current document identities and curated review/access/applicability metadata | Back up; not disposable cache |
| `review/<revision_id>/<extraction_id>/` | New source/extraction snapshots and editable `draft.json` | Keep review work and provenance |
| `approved/<revision_id>/<approval_id>/` | Explicitly approved JSON snapshots and hashes | Keep for publication and reproduction |
| `review/chunks.jsonl` | Default output of the existing PDF review command; not approved | Keep review work; choose a fresh output name for separate experiments |
| `published_artifacts/<document_id>/<revision_id>/<run_id>/` | Source snapshot, chunks and processing manifest from existing PDF publication | Keep provenance; database run status determines success |
| `reports/` | New validation and evaluation reports | Keep important evidence; inspect before cleanup |
| `cache/` | Local model weights, OCR language files and processing caches | Rebuildable, but needed for offline processing |
| `archive/pre-reorganization/extracted/` | Previous extraction outputs, unchanged | Preserve historical evidence and any manual edits |
| `archive/pre-reorganization/reports/` | Previous reports, catalog backups and source synchronization mappings | Back up; not disposable cache |
| `archive/pre-reorganization/relocation/` | File mappings, sizes, SHA-256 hashes and relocation results | Keep for verification and recovery |

Directories are created only when needed. No old artifact is automatically marked
approved or published. Historical manifests retain their original paths and bytes;
use the relocation mapping to find their new locations. Asset-sync records preserve
catalog metadata and file hashes, not the original source file contents.

## Review and approval

The current command writes review output here:

```powershell
python main.py ingest review ./app/assets/synthetic.pdf --skip-embeddings
```

The path is illustrative. Default output is anchored to the repository, regardless
of working directory. Explicit `--output`, `--review-dir` and `--tessdata` relative
paths remain relative to the caller's working directory.

The new `prepare_documents` CLI writes per-revision review packages and provides
`validate` and `approve` subcommands. Edit only `draft.json`; approval creates a
separate snapshot under `approved/`. `publish_documents` embeds that snapshot
exactly and writes publication artifacts. See the [offline indexing guide](../docs/offline-indexing.md)
for commands, JSON fields, supported formats and failure behavior.

The older `ingest publish` command still processes the original PDF and does not
load manually edited JSON. Historical archive JSON is never automatically approved.

## Relocation and recovery

The task-specific `scripts/migrate_indexing_workspace.py` inventories the old
`app/data/` tree before copying. Each run stores `before.json` and `result.json` in
a unique relocation directory. `progress.jsonl` records verified copies and moves
during processing; after an interrupted run, reconcile it with actual file hashes. A source file is removed only after both source
and destination match the recorded SHA-256. Existing destinations are never
overwritten; unreadable or conflicting sources remain in place and are reported.
Rerunning processes remaining source files with a new record. Do not edit sources
or run ingestion concurrently with relocation.

To recover a moved file, use its recorded source/destination mapping, verify its
size and hash, and copy it back only if the old location is unoccupied. Never
overwrite a newer catalog or restore source paths without reconciling the source
snapshot. There is no automatic rollback or runtime fallback to old directories.

See [operation](../README.md), [architecture](../ARCHITECTURE.md),
[catalog](../docs/asset-catalog.md) and [ingestion](../docs/ingestion.md).
