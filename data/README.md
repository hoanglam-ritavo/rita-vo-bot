# Generated local data

This directory is reserved for generated outputs. Original source documents remain in `assets/`.

| Future location | Purpose |
| --- | --- |
| `extracted/` | Extracted text, OCR results, and associated provenance |
| `cache/` | Reusable processing caches |
| `reports/` | Evaluation results and experimental reports |

These subdirectories and the pipelines that produce their contents do not exist yet. Create them only when implementing the corresponding functionality. Git ignores everything here except this README; do not force-add generated or private data.

Automated tests belong in `tests/`. Task-specific manual utilities belong in `scripts/`. See the [project README](../README.md) and [architecture](../ARCHITECTURE.md) for current capabilities and planned work.
