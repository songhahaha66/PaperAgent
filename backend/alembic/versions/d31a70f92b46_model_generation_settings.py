"""Persist explicit connection generation settings without changing existing connections."""
from alembic import op
import sqlalchemy as sa
revision='d31a70f92b46'
down_revision='c8f4a620d931'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('model_configs',sa.Column('generation_settings',sa.JSON(),nullable=False,server_default='{}'))

def downgrade():
    op.drop_column('model_configs','generation_settings')
