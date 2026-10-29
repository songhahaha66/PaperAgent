"""Persist the DeepSeek thinking-mode toggle on model connections."""
from alembic import op
import sqlalchemy as sa

revision = 'c8f4a620d931'
down_revision = 'b28c4e96ad10'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('model_configs', sa.Column('thinking_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('model_configs', 'thinking_enabled')
