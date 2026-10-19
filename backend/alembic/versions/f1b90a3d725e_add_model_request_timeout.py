"""Add optional model-connection request timeout without changing credentials."""
from alembic import op
import sqlalchemy as sa
revision='f1b90a3d725e'
down_revision='e3a40b1c6d29'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('model_configs',sa.Column('timeout_seconds',sa.Float(),nullable=True))

def downgrade():
    op.drop_column('model_configs','timeout_seconds')
