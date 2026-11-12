# Document metadata in PostgreSQL

The local catalog and direct PDF ingestion workflows are retired. Metadata belongs to schema-v2 packages at `app/chunks/<document_id>/<revision_id>/`; PostgreSQL receives it transactionally during publication. See the [chunk package workflow](offline-indexing.md) for initialization, agent-authored extraction/chunking, validation, publication and non-destructive legacy conversion. No approval or reviewer identity is required.

Migration `0003_agent_packages` removes database review status and renames review notes to evidence notes. Run it explicitly before new publication code. Existing chunks, vectors and run history remain intact; local conversion does not change the database.

Historical `indexing_chunks/` is not scanned or moved. Historical source-renaming utilities are not part of this workflow and must not be applied to the current source tree.

For an existing revision, publication locks document ID, source hash, size and extension. Source-path metadata currently permits an explicit `--update-metadata` change when no collision exists; it does not rename files. See [publication consistency](offline-indexing.md#publication-and-consistency) for the distinction from agent authoring rules, and [test setup](../README.md#testing) for integration schema changes and application-table cleanup.
