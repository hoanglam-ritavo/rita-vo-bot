"""Remove approval state and preserve evidence notes.

Downgrade cannot recover removed review statuses; all become pending.
"""
from alembic import op
import sqlalchemy as sa

revision = '0003_agent_packages'
down_revision = '0002_type_a_catalog'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f('ck_document_revisions_review'), 'document_revisions', type_='check')
    op.drop_column('document_revisions', 'review_status')
    op.drop_constraint(op.f('ck_document_revisions_notes_array'), 'document_revisions', type_='check')
    op.alter_column('document_revisions', 'review_notes', new_column_name='evidence_notes')
    op.create_check_constraint(op.f('ck_document_revisions_notes_array'), 'document_revisions', "jsonb_typeof(evidence_notes) = 'array'")


def downgrade():
    op.drop_constraint(op.f('ck_document_revisions_notes_array'), 'document_revisions', type_='check')
    op.alter_column('document_revisions', 'evidence_notes', new_column_name='review_notes')
    op.create_check_constraint(op.f('ck_document_revisions_notes_array'), 'document_revisions', "jsonb_typeof(review_notes) = 'array'")
    op.add_column('document_revisions', sa.Column('review_status', sa.Text(), server_default='pending', nullable=False))
    op.create_check_constraint(op.f('ck_document_revisions_review'), 'document_revisions', "review_status IN ('pending','reviewed')")
