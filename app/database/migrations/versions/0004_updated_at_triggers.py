"""Maintain row update timestamps for both ORM and direct SQL writes."""
from alembic import op

revision = '0004_updated_at_triggers'
down_revision = '0003_agent_packages'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION rita_vo_set_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = statement_timestamp();
            RETURN NEW;
        END;
        $$
    """)
    for table in ('documents', 'document_revisions'):
        op.execute(f"""
            CREATE TRIGGER rita_vo_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION rita_vo_set_updated_at()
        """)


def downgrade():
    for table in ('document_revisions', 'documents'):
        op.execute(f'DROP TRIGGER rita_vo_{table}_updated_at ON {table}')
    op.execute('DROP FUNCTION rita_vo_set_updated_at()')
