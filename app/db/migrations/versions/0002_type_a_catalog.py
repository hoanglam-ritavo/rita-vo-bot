"""Create Type A catalog and ingestion storage."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision = '0002_type_a_catalog'
down_revision = '0001_enable_vector'
branch_labels = None
depends_on = None


def upgrade():
    # Explicit schema snapshot: do not import mutable application models here.
    op.create_table('documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_documents'))
    )
    op.create_table('document_revisions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('source_path', sa.Text(), nullable=False),
    sa.Column('source_path_key', sa.Text(), nullable=False),
    sa.Column('sha256', sa.Text(), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('extension', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('category', sa.Text(), nullable=True),
    sa.Column('document_type', sa.Text(), nullable=True),
    sa.Column('owner', sa.Text(), nullable=True),
    sa.Column('applicability', sa.Text(), nullable=True),
    sa.Column('version_label', sa.Text(), nullable=True),
    sa.Column('effective_from', sa.Date(), nullable=True),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.Column('lifecycle_status', sa.Text(), server_default='unknown', nullable=False),
    sa.Column('review_status', sa.Text(), server_default='pending', nullable=False),
    sa.Column('indexing_decision', sa.Text(), server_default='hold', nullable=False),
    sa.Column('review_notes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('is_catalog_current', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("indexing_decision IN ('hold','include','exclude')", name=op.f('ck_document_revisions_indexing')),
    sa.CheckConstraint("jsonb_typeof(review_notes) = 'array'", name=op.f('ck_document_revisions_notes_array')),
    sa.CheckConstraint("lifecycle_status IN ('unknown','active','superseded','withdrawn','expired')", name=op.f('ck_document_revisions_lifecycle')),
    sa.CheckConstraint("review_status IN ('pending','reviewed')", name=op.f('ck_document_revisions_review')),
    sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_document_revisions_sha256')),
    sa.CheckConstraint('effective_from <= effective_to', name=op.f('ck_document_revisions_effective_dates')),
    sa.CheckConstraint('size_bytes >= 0', name=op.f('ck_document_revisions_size_bytes')),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_document_revisions_document_id_documents')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_revisions'))
    )
    op.create_index(op.f('ix_document_revisions_document_id'), 'document_revisions', ['document_id'], unique=False)
    op.create_index('uq_revision_current_document', 'document_revisions', ['document_id'], unique=True, postgresql_where=sa.text('is_catalog_current'))
    op.create_index('uq_revision_current_path', 'document_revisions', ['source_path_key'], unique=True, postgresql_where=sa.text('is_catalog_current'))
    op.create_table('document_access_rules',
    sa.Column('revision_id', sa.UUID(), nullable=False),
    sa.Column('group_name', sa.Text(), nullable=False),
    sa.CheckConstraint('length(trim(group_name)) > 0', name=op.f('ck_document_access_rules_group_nonempty')),
    sa.ForeignKeyConstraint(['revision_id'], ['document_revisions.id'], name=op.f('fk_document_access_rules_revision_id_document_revisions')),
    sa.PrimaryKeyConstraint('revision_id', 'group_name', name=op.f('pk_document_access_rules'))
    )
    op.create_table('document_relations',
    sa.Column('source_revision_id', sa.UUID(), nullable=False),
    sa.Column('target_revision_id', sa.UUID(), nullable=False),
    sa.Column('relation_type', sa.Text(), nullable=False),
    sa.CheckConstraint("relation_type IN ('duplicate_of','supplements','supersedes','form_for')", name=op.f('ck_document_relations_type')),
    sa.CheckConstraint('source_revision_id <> target_revision_id', name=op.f('ck_document_relations_not_self')),
    sa.ForeignKeyConstraint(['source_revision_id'], ['document_revisions.id'], name=op.f('fk_document_relations_source_revision_id_document_revisions')),
    sa.ForeignKeyConstraint(['target_revision_id'], ['document_revisions.id'], name=op.f('fk_document_relations_target_revision_id_document_revisions')),
    sa.PrimaryKeyConstraint('source_revision_id', 'target_revision_id', 'relation_type', name=op.f('pk_document_relations'))
    )
    op.create_index(op.f('ix_document_relations_target_revision_id'), 'document_relations', ['target_revision_id'], unique=False)
    op.create_table('ingestion_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('revision_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Text(), server_default='pending', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_sha256', sa.Text(), nullable=False),
    sa.Column('configuration', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('model_name', sa.Text(), nullable=True),
    sa.Column('model_revision', sa.Text(), nullable=True),
    sa.Column('embedding_dimensions', sa.Integer(), nullable=True),
    sa.Column('chunk_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error_code', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint("NOT is_active OR status = 'succeeded'", name=op.f('ck_ingestion_runs_active_success')),
    sa.CheckConstraint("jsonb_typeof(configuration) = 'object'", name=op.f('ck_ingestion_runs_configuration_object')),
    sa.CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_ingestion_runs_sha256')),
    sa.CheckConstraint("status IN ('pending','running','succeeded','failed')", name=op.f('ck_ingestion_runs_status')),
    sa.CheckConstraint('chunk_count >= 0', name=op.f('ck_ingestion_runs_chunk_count')),
    sa.CheckConstraint('embedding_dimensions = 384', name=op.f('ck_ingestion_runs_dimensions')),
    sa.CheckConstraint('started_at <= finished_at', name=op.f('ck_ingestion_runs_times')),
    sa.ForeignKeyConstraint(['revision_id'], ['document_revisions.id'], name=op.f('fk_ingestion_runs_revision_id_document_revisions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ingestion_runs'))
    )
    op.create_index(op.f('ix_ingestion_runs_revision_id'), 'ingestion_runs', ['revision_id'], unique=False)
    op.create_index('uq_run_active_revision', 'ingestion_runs', ['revision_id'], unique=True, postgresql_where=sa.text('is_active'))
    op.create_table('document_chunks',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('source_location', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('extraction_method', sa.Text(), nullable=False),
    sa.Column('embedding', Vector(384), nullable=True),
    sa.CheckConstraint("extraction_method IN ('native','ocr')", name=op.f('ck_document_chunks_extraction_method')),
    sa.CheckConstraint("jsonb_typeof(source_location) = 'object'", name=op.f('ck_document_chunks_location_object')),
    sa.CheckConstraint('chunk_index >= 0', name=op.f('ck_document_chunks_chunk_index')),
    sa.CheckConstraint('length(trim(content)) > 0', name=op.f('ck_document_chunks_content_nonempty')),
    sa.ForeignKeyConstraint(['run_id'], ['ingestion_runs.id'], name=op.f('fk_document_chunks_run_id_ingestion_runs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_chunks')),
    sa.UniqueConstraint('run_id', 'chunk_index', name=op.f('uq_document_chunks_run_id'))
    )
    op.create_index(op.f('ix_document_chunks_run_id'), 'document_chunks', ['run_id'], unique=False)


def downgrade():
    op.drop_table('document_chunks')
    op.drop_table('ingestion_runs')
    op.drop_table('document_relations')
    op.drop_table('document_access_rules')
    op.drop_table('document_revisions')
    op.drop_table('documents')
