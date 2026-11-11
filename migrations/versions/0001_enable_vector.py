"""Enable pgvector without creating application tables."""
from alembic import op

revision = '0001_enable_vector'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')


def downgrade():
    # Shared extension: never remove it automatically.
    pass
