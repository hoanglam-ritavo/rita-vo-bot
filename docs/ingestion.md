# Document ingestion

The local catalog and direct PDF ingestion workflows are retired. Metadata belongs to schema-v2 packages at `chunks/<document_id>/`; PostgreSQL receives it transactionally during publication. See the [chunk package workflow](offline-indexing.md) for initialization, agent-authored extraction/chunking, validation, publication and non-destructive legacy conversion. No approval or reviewer identity is required.

Start with [Scope 1: source preparation and package authoring](offline-indexing.md#agent-authoring-procedure). Its output is the complete four-file v2 package, manually checked for source fidelity, item coverage and relevant context, then structurally validated without a model or database. Detail, inventory and optional group chunks use existing fields; they do not introduce runtime chunk types or automatic completeness checks.

Follow the [Scope 2 operational guide](offline-indexing.md#scope-2-package-reading-embedding-and-publication) for package reading, [technical preflight](offline-indexing.md#technical-preflight), database-aware dry-run, explicit publication and result verification. This is a recommended sequence, not a persisted preflight prerequisite. Optional preflight embeddings are discarded, and dry-run does not tokenize or encode. Oversized input returns to authoring for revision without losing meaning. Passing Scope 1 does not establish model compatibility, publication eligibility or retrieval quality; Online routing, answering and fallback are separate concerns.

Migration `0003_agent_packages` removes database review status and renames review notes to evidence notes. Run it explicitly before new publication code. Existing chunks, vectors and run history remain intact; local conversion does not change the database.

Historical `indexing_chunks/` is not scanned or moved. Historical source-renaming utilities are not part of this workflow and must not be applied to the current source tree.

Conversion is conditional on the selected legacy package meeting the stricter v2 extraction/chunk contract; missing XLSX sheet names and unsupported location fields are not inferred or silently removed. A batch can publish independent revisions while rejecting others, so a nonzero exit code is not a batch-wide rollback. See the [detailed commands](offline-indexing.md#commands), [conversion limits](offline-indexing.md#legacy-conversion-and-rollout) and [verification boundaries](offline-indexing.md#verification).
