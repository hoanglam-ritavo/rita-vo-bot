# Local catalog and generated data

This directory holds the curated local catalog and generated outputs. Original source documents remain in `assets/`. The catalog is durable administrative data: back it up to approved private storage before manual changes. It must not be deleted as cache.

| Location | Purpose |
| --- | --- |
| `catalog/documents.json` | Current source inventory and manually reviewed metadata; created by the catalog CLI, not disposable |
| `extracted/` | Extracted text, OCR results, and associated provenance |
| `cache/` | Reusable processing caches |
| `reports/` | Catalog checks, evaluation results and experimental reports |
| `reports/asset-sync/<run_id>/` | Durable catalog backup, source path mapping and verification records for a local synchronization |

The standalone PDF example creates these directories as needed: JSONL under `extracted/`, local OCR language files and model downloads under `cache/`, and optional review artifacts under `reports/`. Single-PDF ingestion publication is implemented; see [the ingestion guide](../docs/ingestion.md). Git ignores everything here except this README; do not force-add generated or private data.

Automated tests belong in `tests/`. Task-specific manual utilities belong in `scripts/`. See the [project README](../README.md) and [architecture](../ARCHITECTURE.md) for current capabilities and planned work.

Keep asset synchronization records in approved private backups, not disposable
cache. Each run retains the exact original catalog (`documents.before.json`), a
mapping/report (`report.json`) with identities, fingerprints and removed records,
and pre-update file hashes (`files.before.json`). These records do not back up
source file contents. Do not restore an old catalog without reconciling its source
paths with the intended source snapshot.

Existing extraction JSONL and OCR review manifests retain their original `source`
paths as processing provenance. Use the synchronization mapping, and earlier
folder-migration records where needed, to locate current files. Do not rewrite
historical output or rerun extraction solely because a source directory moved.

See the [catalog guide](../docs/asset-catalog.md) for initialization, validation, schema and maintenance. Catalog/report contents remain ignored by Git. Published extraction runs use `extracted/<document_id>/<revision_id>/<run_id>/` with a source PDF snapshot, JSONL and provenance/configuration manifest. PostgreSQL run status determines publication, not artifact presence. Failed runs may leave private artifacts; interrupted processes may leave a running database record. Existing experimental JSONL files are preserved.
