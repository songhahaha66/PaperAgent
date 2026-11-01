"""Persist user profile avatars."""
from alembic import op
import sqlalchemy as sa

revision = 'a40d8f9e2b61'
down_revision = 'd31a70f92b46'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('avatar_url', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('users', 'avatar_url')
