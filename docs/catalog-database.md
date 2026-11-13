# Document metadata in PostgreSQL

The local catalog and direct PDF ingestion workflows are retired. Metadata belongs to schema-v2/v3 packages at `chunks/<document_id>/`; PostgreSQL receives it transactionally during publication. See the [chunk package workflow](offline-indexing.md) for initialization, agent-authored extraction/chunking, validation, publication and non-destructive legacy conversion. No approval or reviewer identity is required.

Migration `0003_agent_packages` removes database review status and renames review notes to evidence notes. Run it explicitly before new publication code. Existing chunks, vectors and run history remain intact; local conversion does not change the database.

Migration `0005_structured_evidence` additionally stores nullable chunk author IDs/roles and run-owned scopes, items and typed values. Structure and vectors share the publication transaction and fingerprint. Read [the v3 contract](structured-evidence.md) before publishing or querying structured evidence. V2 text conventions alone still do not infer hierarchy.

Historical `indexing_chunks/` is not scanned or moved. Historical source-renaming utilities are not part of this workflow and must not be applied to the current source tree.

Scope 2 publishes document/revision metadata, access rules, resolved revision relations, an ingestion run and its chunks in one transaction per revision. Chunk rows store generated IDs, run ID, array-derived `chunk_index`, content, source spans, extraction method and vector. The passage prefix belongs to embedding input, not stored content. Optional chunk notes, raw extraction and source snapshots remain package artifacts rather than dedicated chunk columns. See the [full input-to-storage mapping](offline-indexing.md#embedding-contract-and-persisted-data); Scope 1 inventory/group conventions do not create typed hierarchy in the database.

`is_catalog_current` selects the document's current revision; `is_active` selects a run within a revision. A superseded revision can retain an active historical run. Neither flag alone establishes current applicability or public retrieval eligibility, which also depends on lifecycle, effective dates, access and model compatibility. An identical-run skip is not a new publication. Count/vector invariants are enforced by the publication pipeline, not all by database constraints; other writers must not assume equivalent guarantees.

For an existing revision, publication locks document ID, source hash, size and extension. Source-path metadata currently permits an explicit `--update-metadata` change when no collision exists; it does not rename files. See [publication consistency](offline-indexing.md#publication-and-consistency) for the distinction from agent authoring rules, and [test setup](../README.md#testing) for integration schema changes and application-table cleanup.
