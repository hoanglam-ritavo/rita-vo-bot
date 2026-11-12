# Document metadata

The local catalog and direct PDF ingestion workflows are retired. Metadata belongs to schema-v2 packages at `app/chunks/<document_id>/<revision_id>/`; PostgreSQL receives it transactionally during publication. See the [chunk package workflow](offline-indexing.md) for initialization, agent-authored extraction/chunking, validation, publication and non-destructive legacy conversion. No approval or reviewer identity is required.

Migration `0003_agent_packages` removes database review status and renames review notes to evidence notes. Run it explicitly before new publication code. Existing chunks, vectors and run history remain intact; local conversion does not change the database.

Historical `indexing_chunks/` is not scanned or moved. Historical source-renaming utilities are not part of this workflow and must not be applied to the current source tree.

The legacy catalog validator is retained for explicit legacy conversion and historical source utilities. Tests for that compatibility do not restore a catalog-based preparation workflow. Converter eligibility and v2 compatibility limits are described in the [indexing guide](offline-indexing.md#legacy-conversion-and-rollout).
