# Document metadata

The local catalog and direct PDF ingestion workflows are retired. Metadata belongs to packages under `app/chunks/review/` and `app/chunks/approved/`. See the [chunk package workflow](offline-indexing.md) for agent preparation, optional initialization/extraction, metadata review and historical-workspace limitations. PostgreSQL receives package metadata transactionally during publication.

Historical source-renaming utilities are not part of this workflow and must not be applied to the current source tree.
