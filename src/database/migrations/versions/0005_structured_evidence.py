"""Add run-owned v3 evidence without rewriting historical chunks.

Revision ID: 0005_structured_evidence
Revises: 0004_updated_at_triggers
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005_structured_evidence'
down_revision = '0004_updated_at_triggers'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('document_chunks', sa.Column('author_id', sa.Text(), nullable=True))
    op.add_column('document_chunks', sa.Column('role', sa.Text(), nullable=True))
    op.create_unique_constraint('uq_chunk_run_author', 'document_chunks', ['run_id', 'author_id'])
    for name in ('evidence_scopes', 'evidence_items'):
        op.create_table(name,
            sa.Column('run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ingestion_runs.id'), primary_key=True),
            sa.Column('author_id', sa.Text(), primary_key=True),
            sa.Column('data', postgresql.JSONB(), nullable=False))
    op.create_table('evidence_values',
        sa.Column('run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ingestion_runs.id'), primary_key=True),
        sa.Column('author_id', sa.Text(), primary_key=True),
        sa.Column('item_id', sa.Text(), nullable=False),
        sa.Column('numeric_value', sa.Numeric(), nullable=True),
        sa.Column('data', postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(['run_id', 'item_id'], ['evidence_items.run_id', 'evidence_items.author_id']))


def downgrade():
    op.drop_table('evidence_values')
    op.drop_table('evidence_items')
    op.drop_table('evidence_scopes')
    op.drop_constraint('uq_chunk_run_author', 'document_chunks', type_='unique')
    op.drop_column('document_chunks', 'role')
    op.drop_column('document_chunks', 'author_id')
