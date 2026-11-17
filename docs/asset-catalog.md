# Document metadata

The local catalog and direct PDF ingestion workflows are retired. Metadata belongs to schema-v2/v3 packages at `chunks/<document_id>/`; PostgreSQL receives it transactionally during publication. See the [chunk package workflow](offline-indexing.md) for initialization, agent-authored extraction/chunking, validation, publication and non-destructive legacy conversion. No approval or reviewer identity is required.

The current baseline stores evidence notes without review status. The former `0003_agent_packages` migration belongs to retired history and cannot run in this checkout. Follow [database initialization and boundaries](database-design.md#initialization-and-boundaries) for the `bce4184a7f51` baseline and additive `c7d92e6a104b` guards. Local package conversion does not migrate a database.

Historical `indexing_chunks/` is not scanned or moved. Historical source-renaming utilities are not part of this workflow and must not be applied to the current source tree.

The legacy catalog validator is retained for explicit legacy conversion and historical source utilities. Tests for that compatibility do not restore a catalog-based preparation workflow. Converter eligibility and v2 compatibility limits are described in the [indexing guide](offline-indexing.md#legacy-conversion-and-rollout).
