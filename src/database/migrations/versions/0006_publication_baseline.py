"""Pre-production schema baseline. Refuses databases containing application data.

This is deliberately not a data conversion. Unrelated tables/vector are preserved.
The DDL below is an explicit snapshot, not imports of runtime model metadata.
"""
from alembic import op
from importlib import import_module

revision = '0006_publication_baseline'
down_revision = '0005_structured_evidence'
branch_labels = None
depends_on = None


def require_empty(names):
    # Lock all application tables before checking: no writer can race the guard.
    op.execute('LOCK TABLE ' + ', '.join(names) + ' IN ACCESS EXCLUSIVE MODE')
    for name in names:
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {name} LIMIT 1) THEN
                RAISE EXCEPTION 'Fresh empty application schema required; provision a separate database. No data conversion is supplied.';
            END IF;
        END; $$""")


def upgrade():
    require_empty(['document_chunks', 'evidence_values', 'evidence_items', 'evidence_scopes', 'ingestion_runs', 'document_relations', 'document_access_rules', 'document_revisions', 'documents'])
    op.drop_table('document_chunks')
    op.drop_table('evidence_values')
    op.drop_table('evidence_items')
    op.drop_table('evidence_scopes')
    op.drop_table('ingestion_runs')
    op.drop_table('document_relations')
    op.drop_table('document_access_rules')
    op.drop_table('document_revisions')
    op.drop_table('documents')
    op.execute('CREATE TABLE documents (\n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_documents PRIMARY KEY (id)\n)')
    op.execute("CREATE TABLE document_revisions (\n\tid UUID NOT NULL, \n\tdocument_id UUID NOT NULL, \n\tsource_path TEXT NOT NULL, \n\tsource_path_key TEXT NOT NULL, \n\tsha256 TEXT NOT NULL, \n\tsize_bytes BIGINT NOT NULL, \n\textension TEXT NOT NULL, \n\ttitle TEXT, \n\tcategory TEXT, \n\tdocument_type TEXT, \n\towner TEXT, \n\tapplicability TEXT, \n\tversion_label TEXT, \n\teffective_from DATE, \n\teffective_to DATE, \n\tlifecycle_status TEXT DEFAULT 'unknown' NOT NULL, \n\tindexing_decision TEXT DEFAULT 'hold' NOT NULL, \n\tevidence_notes JSONB DEFAULT '[]'::jsonb NOT NULL, \n\tis_catalog_current BOOLEAN DEFAULT false NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tCONSTRAINT pk_document_revisions PRIMARY KEY (id), \n\tCONSTRAINT ck_document_revisions_source_nonempty CHECK (length(trim(source_path)) > 0 AND length(trim(source_path_key)) > 0 AND length(trim(extension)) > 0), \n\tCONSTRAINT ck_document_revisions_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_document_revisions_size_bytes CHECK (size_bytes >= 0), \n\tCONSTRAINT ck_document_revisions_effective_dates CHECK (effective_from <= effective_to), \n\tCONSTRAINT ck_document_revisions_lifecycle CHECK (lifecycle_status IN ('unknown','active','superseded','withdrawn','expired')), \n\tCONSTRAINT ck_document_revisions_indexing CHECK (indexing_decision IN ('hold','include','exclude')), \n\tCONSTRAINT ck_document_revisions_notes_array CHECK (jsonb_typeof(evidence_notes) = 'array'), \n\tCONSTRAINT fk_document_revisions_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id)\n)")
    op.execute('CREATE INDEX ix_document_revisions_document_id ON document_revisions (document_id)')
    op.execute('CREATE UNIQUE INDEX uq_revision_current_document ON document_revisions (document_id) WHERE is_catalog_current')
    op.execute('CREATE UNIQUE INDEX uq_revision_current_path ON document_revisions (source_path_key) WHERE is_catalog_current')
    op.execute('CREATE TABLE document_access_rules (\n\trevision_id UUID NOT NULL, \n\tgroup_name TEXT NOT NULL, \n\tCONSTRAINT pk_document_access_rules PRIMARY KEY (revision_id, group_name), \n\tCONSTRAINT ck_document_access_rules_group_nonempty CHECK (length(trim(group_name)) > 0), \n\tCONSTRAINT fk_document_access_rules_revision_id_document_revisions FOREIGN KEY(revision_id) REFERENCES document_revisions (id)\n)')
    op.execute("CREATE TABLE document_relations (\n\tsource_revision_id UUID NOT NULL, \n\ttarget_revision_id UUID NOT NULL, \n\trelation_type TEXT NOT NULL, \n\tCONSTRAINT pk_document_relations PRIMARY KEY (source_revision_id, target_revision_id, relation_type), \n\tCONSTRAINT ck_document_relations_not_self CHECK (source_revision_id <> target_revision_id), \n\tCONSTRAINT ck_document_relations_type CHECK (relation_type IN ('duplicate_of','supplements','supersedes','form_for')), \n\tCONSTRAINT fk_document_relations_source_revision_id_document_revisions FOREIGN KEY(source_revision_id) REFERENCES document_revisions (id), \n\tCONSTRAINT fk_document_relations_target_revision_id_document_revisions FOREIGN KEY(target_revision_id) REFERENCES document_revisions (id)\n)")
    op.execute('CREATE INDEX ix_document_relations_target_revision_id ON document_relations (target_revision_id)')
    op.execute("CREATE TABLE ingestion_runs (\n\tid UUID NOT NULL, \n\trevision_id UUID NOT NULL, \n\tstatus TEXT DEFAULT 'succeeded' NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tsource_sha256 TEXT NOT NULL, \n\tconfiguration JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tmodel_name TEXT NOT NULL, \n\tmodel_revision TEXT NOT NULL, \n\tembedding_dimensions INTEGER NOT NULL, \n\tchunk_count INTEGER DEFAULT '0' NOT NULL, \n\tis_active BOOLEAN DEFAULT false NOT NULL, \n\tCONSTRAINT pk_ingestion_runs PRIMARY KEY (id), \n\tCONSTRAINT ck_ingestion_runs_status CHECK (status = 'succeeded'), \n\tCONSTRAINT ck_ingestion_runs_active_success CHECK (NOT is_active OR status = 'succeeded'), \n\tCONSTRAINT ck_ingestion_runs_sha256 CHECK (source_sha256 ~ '^[0-9a-f]{64}$'), \n\tCONSTRAINT ck_ingestion_runs_chunk_count CHECK (chunk_count >= 0), \n\tCONSTRAINT ck_ingestion_runs_dimensions CHECK (embedding_dimensions = 384), \n\tCONSTRAINT ck_ingestion_runs_model_name CHECK (length(trim(model_name)) > 0), \n\tCONSTRAINT ck_ingestion_runs_model_revision CHECK (model_revision ~ '^[0-9a-f]{40}$'), \n\tCONSTRAINT ck_ingestion_runs_configuration_object CHECK (jsonb_typeof(configuration) = 'object'), \n\tCONSTRAINT fk_ingestion_runs_revision_id_document_revisions FOREIGN KEY(revision_id) REFERENCES document_revisions (id)\n)")
    op.execute('CREATE INDEX ix_ingestion_runs_revision_id ON ingestion_runs (revision_id)')
    op.execute('CREATE UNIQUE INDEX uq_run_active_revision ON ingestion_runs (revision_id) WHERE is_active')
    op.execute("CREATE TABLE document_chunks (\n\tid UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tchunk_index INTEGER NOT NULL, \n\tcontent TEXT NOT NULL, \n\tauthor_id TEXT, \n\trole TEXT, \n\tsource_location JSONB NOT NULL, \n\textraction_method TEXT NOT NULL, \n\tembedding VECTOR(384), \n\tCONSTRAINT pk_document_chunks PRIMARY KEY (id), \n\tCONSTRAINT uq_document_chunks_run_id UNIQUE (run_id, chunk_index), \n\tCONSTRAINT uq_chunk_run_author UNIQUE (run_id, author_id), \n\tCONSTRAINT ck_document_chunks_chunk_index CHECK (chunk_index >= 0), \n\tCONSTRAINT ck_document_chunks_identity_role CHECK ((author_id IS NULL AND role IS NULL) OR (author_id IS NOT NULL AND role IS NOT NULL AND author_id ~ '^[a-zA-Z0-9_.-]{1,100}$' AND role IN ('definition','rule','procedure','inventory','detail','table','administrative'))), \n\tCONSTRAINT ck_document_chunks_content_nonempty CHECK (length(trim(content)) > 0), \n\tCONSTRAINT ck_document_chunks_location_object CHECK (jsonb_typeof(source_location) = 'object'), \n\tCONSTRAINT ck_document_chunks_extraction_method CHECK (extraction_method IN ('native','ocr')), \n\tCONSTRAINT fk_document_chunks_run_id_ingestion_runs FOREIGN KEY(run_id) REFERENCES ingestion_runs (id)\n)")
    op.execute('CREATE INDEX ix_document_chunks_run_id ON document_chunks (run_id)')
    op.execute("CREATE TABLE evidence_items (\n\trun_id UUID NOT NULL, \n\tauthor_id TEXT NOT NULL, \n\tparent_id TEXT, \n\tdata JSONB NOT NULL, \n\tCONSTRAINT pk_evidence_items PRIMARY KEY (run_id, author_id), \n\tCONSTRAINT fk_evidence_items_run_id_evidence_items FOREIGN KEY(run_id, parent_id) REFERENCES evidence_items (run_id, author_id) DEFERRABLE INITIALLY DEFERRED, \n\tCONSTRAINT ck_evidence_items_not_self CHECK (parent_id IS NULL OR parent_id <> author_id), \n\tCONSTRAINT ck_evidence_items_snapshot CHECK (jsonb_typeof(data) = 'object' AND data->>'id' IS NOT NULL AND data->>'id' = author_id AND (data->>'parent_id') IS NOT DISTINCT FROM parent_id), \n\tCONSTRAINT ck_evidence_items_author_id CHECK (author_id ~ '^[a-zA-Z0-9_.-]{1,100}$'), \n\tCONSTRAINT fk_evidence_items_run_id_ingestion_runs FOREIGN KEY(run_id) REFERENCES ingestion_runs (id)\n)")
    op.execute("CREATE TABLE evidence_scopes (\n\trun_id UUID NOT NULL, \n\tauthor_id TEXT NOT NULL, \n\tdata JSONB NOT NULL, \n\tCONSTRAINT pk_evidence_scopes PRIMARY KEY (run_id, author_id), \n\tCONSTRAINT ck_evidence_scopes_snapshot CHECK (jsonb_typeof(data) = 'object' AND data->>'id' IS NOT NULL AND data->>'id' = author_id), \n\tCONSTRAINT ck_evidence_scopes_author_id CHECK (author_id ~ '^[a-zA-Z0-9_.-]{1,100}$'), \n\tCONSTRAINT fk_evidence_scopes_run_id_ingestion_runs FOREIGN KEY(run_id) REFERENCES ingestion_runs (id)\n)")
    op.execute("CREATE TABLE publication_attempts (\n\tid UUID NOT NULL, \n\tdeclared_revision_id UUID NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tfinished_at TIMESTAMP WITH TIME ZONE, \n\tstatus TEXT DEFAULT 'running' NOT NULL, \n\toutcome TEXT, \n\terror_code TEXT, \n\trun_id UUID, \n\tCONSTRAINT pk_publication_attempts PRIMARY KEY (id), \n\tCONSTRAINT ck_publication_attempts_status CHECK (status IN ('running','succeeded','failed','cancelled')), \n\tCONSTRAINT ck_publication_attempts_outcome CHECK (outcome IS NULL OR outcome IN ('published','unchanged','metadata_updated')), \n\tCONSTRAINT ck_publication_attempts_receipt CHECK ((outcome IS NULL AND run_id IS NULL) OR (outcome IS NOT NULL AND run_id IS NOT NULL)), \n\tCONSTRAINT ck_publication_attempts_terminal_time CHECK ((status = 'running' AND finished_at IS NULL) OR (status <> 'running' AND finished_at IS NOT NULL)), \n\tCONSTRAINT ck_publication_attempts_success CHECK (status <> 'succeeded' OR (outcome IS NOT NULL AND error_code IS NULL)), \n\tCONSTRAINT ck_publication_attempts_failure CHECK (status NOT IN ('failed','cancelled') OR (outcome IS NULL AND error_code IS NOT NULL)), \n\tCONSTRAINT ck_publication_attempts_times CHECK (finished_at IS NULL OR started_at <= finished_at), \n\tCONSTRAINT fk_publication_attempts_run_id_ingestion_runs FOREIGN KEY(run_id) REFERENCES ingestion_runs (id)\n)")
    op.execute('CREATE INDEX ix_publication_attempts_declared_revision_id ON publication_attempts (declared_revision_id)')
    op.execute('CREATE INDEX ix_publication_attempts_run_id ON publication_attempts (run_id)')
    op.execute('CREATE TABLE evidence_item_chunks (\n\trun_id UUID NOT NULL, \n\titem_id TEXT NOT NULL, \n\tchunk_id TEXT NOT NULL, \n\tposition INTEGER NOT NULL, \n\tCONSTRAINT pk_evidence_item_chunks PRIMARY KEY (run_id, item_id, chunk_id), \n\tCONSTRAINT fk_evidence_item_chunks_run_id_evidence_items FOREIGN KEY(run_id, item_id) REFERENCES evidence_items (run_id, author_id), \n\tCONSTRAINT fk_evidence_item_chunks_run_id_document_chunks FOREIGN KEY(run_id, chunk_id) REFERENCES document_chunks (run_id, author_id), \n\tCONSTRAINT uq_evidence_item_chunks_run_id UNIQUE (run_id, item_id, position), \n\tCONSTRAINT ck_evidence_item_chunks_position CHECK (position >= 0)\n)')
    op.execute('CREATE TABLE evidence_scope_chunks (\n\trun_id UUID NOT NULL, \n\tscope_id TEXT NOT NULL, \n\tchunk_id TEXT NOT NULL, \n\tposition INTEGER NOT NULL, \n\tCONSTRAINT pk_evidence_scope_chunks PRIMARY KEY (run_id, scope_id, chunk_id), \n\tCONSTRAINT fk_evidence_scope_chunks_run_id_evidence_scopes FOREIGN KEY(run_id, scope_id) REFERENCES evidence_scopes (run_id, author_id), \n\tCONSTRAINT fk_evidence_scope_chunks_run_id_document_chunks FOREIGN KEY(run_id, chunk_id) REFERENCES document_chunks (run_id, author_id), \n\tCONSTRAINT uq_evidence_scope_chunks_run_id UNIQUE (run_id, scope_id, position), \n\tCONSTRAINT ck_evidence_scope_chunks_position CHECK (position >= 0)\n)')
    op.execute('CREATE TABLE evidence_scope_items (\n\trun_id UUID NOT NULL, \n\tscope_id TEXT NOT NULL, \n\titem_id TEXT NOT NULL, \n\tposition INTEGER NOT NULL, \n\tCONSTRAINT pk_evidence_scope_items PRIMARY KEY (run_id, scope_id, item_id), \n\tCONSTRAINT fk_evidence_scope_items_run_id_evidence_scopes FOREIGN KEY(run_id, scope_id) REFERENCES evidence_scopes (run_id, author_id), \n\tCONSTRAINT fk_evidence_scope_items_run_id_evidence_items FOREIGN KEY(run_id, item_id) REFERENCES evidence_items (run_id, author_id), \n\tCONSTRAINT uq_evidence_scope_items_run_id UNIQUE (run_id, scope_id, position), \n\tCONSTRAINT ck_evidence_scope_items_position CHECK (position >= 0)\n)')
    op.execute("CREATE TABLE evidence_values (\n\trun_id UUID NOT NULL, \n\tauthor_id TEXT NOT NULL, \n\titem_id TEXT NOT NULL, \n\tnumeric_value NUMERIC, \n\tdata JSONB NOT NULL, \n\tCONSTRAINT pk_evidence_values PRIMARY KEY (run_id, author_id), \n\tCONSTRAINT fk_evidence_values_run_id_evidence_items FOREIGN KEY(run_id, item_id) REFERENCES evidence_items (run_id, author_id), \n\tCONSTRAINT ck_evidence_values_author_id CHECK (author_id ~ '^[a-zA-Z0-9_.-]{1,100}$'), \n\tCONSTRAINT ck_evidence_values_snapshot CHECK (jsonb_typeof(data) = 'object' AND data->>'id' IS NOT NULL AND data->>'id' = author_id AND data->>'item_id' IS NOT NULL AND data->>'item_id' = item_id), \n\tCONSTRAINT ck_evidence_values_value_type CHECK (data->>'type' IS NOT NULL AND data->>'type' IN ('text','boolean','decimal','date')), \n\tCONSTRAINT ck_evidence_values_numeric_projection CHECK (CASE WHEN data->>'type' = 'decimal' THEN numeric_value IS NOT NULL AND data->>'value' ~ '^-?[0-9]+([.][0-9]+)?$' AND numeric_value = (data->>'value')::numeric ELSE numeric_value IS NULL END), \n\tCONSTRAINT fk_evidence_values_run_id_ingestion_runs FOREIGN KEY(run_id) REFERENCES ingestion_runs (id)\n)")
    op.execute("CREATE TABLE metadata_audits (\n\tid UUID NOT NULL, \n\trevision_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\trecorded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tevent TEXT NOT NULL, \n\tbefore JSONB, \n\tafter JSONB NOT NULL, \n\tCONSTRAINT pk_metadata_audits PRIMARY KEY (id), \n\tCONSTRAINT ck_metadata_audits_event CHECK (event IN ('created','updated','superseded')), \n\tCONSTRAINT ck_metadata_audits_before_object CHECK (before IS NULL OR jsonb_typeof(before) = 'object'), \n\tCONSTRAINT ck_metadata_audits_after_object CHECK (jsonb_typeof(after) = 'object'), \n\tCONSTRAINT ck_metadata_audits_creation CHECK ((event = 'created') = (before IS NULL)), \n\tCONSTRAINT ck_metadata_audits_changed CHECK (before IS DISTINCT FROM after), \n\tCONSTRAINT fk_metadata_audits_revision_id_document_revisions FOREIGN KEY(revision_id) REFERENCES document_revisions (id), \n\tCONSTRAINT fk_metadata_audits_attempt_id_publication_attempts FOREIGN KEY(attempt_id) REFERENCES publication_attempts (id)\n)")
    op.execute('CREATE INDEX ix_metadata_audits_attempt_id ON metadata_audits (attempt_id)')
    op.execute('CREATE INDEX ix_metadata_audits_revision_id ON metadata_audits (revision_id)')
    for table in ('documents', 'document_revisions'):
        op.execute(f"CREATE TRIGGER rita_vo_{table}_updated_at BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION rita_vo_set_updated_at()")
    op.execute("""
        CREATE FUNCTION rita_vo_immutable_revision_identity() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF ROW(NEW.id, NEW.document_id, NEW.sha256, NEW.size_bytes, NEW.extension)
                IS DISTINCT FROM ROW(OLD.id, OLD.document_id, OLD.sha256, OLD.size_bytes, OLD.extension) THEN
                RAISE EXCEPTION 'revision_identity_immutable';
            END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("CREATE TRIGGER rita_vo_revision_identity BEFORE UPDATE ON document_revisions FOR EACH ROW EXECUTE FUNCTION rita_vo_immutable_revision_identity()")
    op.execute("""
        CREATE FUNCTION rita_vo_append_only_metadata() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'metadata_audit_append_only';
        END; $$
    """)
    op.execute("CREATE TRIGGER rita_vo_metadata_append_only BEFORE UPDATE OR DELETE ON metadata_audits FOR EACH ROW EXECUTE FUNCTION rita_vo_append_only_metadata()")


def downgrade():
    require_empty(['documents', 'document_revisions', 'document_access_rules', 'document_relations', 'ingestion_runs', 'document_chunks', 'evidence_items', 'evidence_scopes', 'publication_attempts', 'evidence_item_chunks', 'evidence_scope_chunks', 'evidence_scope_items', 'evidence_values', 'metadata_audits'])
    op.drop_table('metadata_audits')
    op.drop_table('evidence_values')
    op.drop_table('evidence_scope_items')
    op.drop_table('evidence_scope_chunks')
    op.drop_table('evidence_item_chunks')
    op.drop_table('publication_attempts')
    op.drop_table('evidence_scopes')
    op.drop_table('evidence_items')
    op.drop_table('document_chunks')
    op.drop_table('ingestion_runs')
    op.drop_table('document_relations')
    op.drop_table('document_access_rules')
    op.drop_table('document_revisions')
    op.drop_table('documents')
    op.execute('DROP FUNCTION rita_vo_immutable_revision_identity()')
    op.execute('DROP FUNCTION rita_vo_append_only_metadata()')
    op.execute('DROP FUNCTION rita_vo_set_updated_at()')
    # Rebuild the empty historical schema from its immutable DDL revisions.
    for module in ('0002_type_a_catalog', '0003_agent_packages', '0004_updated_at_triggers', '0005_structured_evidence'):
        import_module('src.database.migrations.versions.' + module).upgrade()
