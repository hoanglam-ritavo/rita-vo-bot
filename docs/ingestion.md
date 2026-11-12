# Document ingestion

The local catalog and direct PDF ingestion workflows are retired. Use the [chunk package workflow](offline-indexing.md) for primary agent-authored preparation, optional loader/chunker commands, validation, human review, approval and publication. New packages live in `app/chunks/review/` and `app/chunks/approved/`; historical `indexing_chunks/` data is not moved or scanned automatically. Metadata is stored in each package; PostgreSQL receives it transactionally during publication.

Historical source-renaming utilities are not part of this workflow and must not be applied to the current source tree.
