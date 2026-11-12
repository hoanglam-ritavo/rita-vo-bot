# Document metadata in PostgreSQL

The local catalog and direct PDF ingestion workflows are retired. Review and approved package metadata lives under `app/chunks/`; PostgreSQL receives it transactionally during publication. See the [chunk package workflow](offline-indexing.md) for agent preparation, validation, approval and publication requirements. Changing the workspace requires no database schema migration and does not relocate historical packages.

Historical source-renaming utilities are not part of this workflow and must not be applied to the current source tree.
