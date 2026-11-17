"""Restore publication guards after the provisioned schema baseline.

Revision ID: c7d92e6a104b
Revises: bce4184a7f51

Fresh databases must enable vector before the baseline creates vector columns.
This additive revision never rebuilds tables or rewrites existing rows.
"""
from alembic import op

revision = 'c7d92e6a104b'
down_revision = 'bce4184a7f51'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.execute("""
        CREATE FUNCTION rita_vo_set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            NEW.updated_at = statement_timestamp();
            RETURN NEW;
        END; $$
    """)
    for table in ('documents', 'document_revisions'):
        op.execute(f'CREATE TRIGGER rita_vo_{table}_updated_at BEFORE UPDATE ON {table} '
                   'FOR EACH ROW EXECUTE FUNCTION rita_vo_set_updated_at()')
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
    op.execute('CREATE TRIGGER rita_vo_revision_identity BEFORE UPDATE ON document_revisions '
               'FOR EACH ROW EXECUTE FUNCTION rita_vo_immutable_revision_identity()')
    op.execute("""
        CREATE FUNCTION rita_vo_append_only_metadata() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'metadata_audit_append_only';
        END; $$
    """)
    op.execute('CREATE TRIGGER rita_vo_metadata_append_only BEFORE UPDATE OR DELETE ON metadata_audits '
               'FOR EACH ROW EXECUTE FUNCTION rita_vo_append_only_metadata()')


def downgrade():
    op.execute('DROP TRIGGER rita_vo_metadata_append_only ON metadata_audits')
    op.execute('DROP TRIGGER rita_vo_revision_identity ON document_revisions')
    for table in ('document_revisions', 'documents'):
        op.execute(f'DROP TRIGGER rita_vo_{table}_updated_at ON {table}')
    op.execute('DROP FUNCTION rita_vo_append_only_metadata()')
    op.execute('DROP FUNCTION rita_vo_immutable_revision_identity()')
    op.execute('DROP FUNCTION rita_vo_set_updated_at()')
