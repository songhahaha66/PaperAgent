"""Keep hidden projects accessible without listing them by default."""
from alembic import op
import sqlalchemy as sa

revision = 'f74c920d613b'
down_revision = 'a40d8f9e2b61'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('projects', sa.Column('is_hidden', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('projects', 'is_hidden')
