"""Historical projects remain export-only; new projects opt into persistent storage."""
from alembic import op
import sqlalchemy as sa
revision = 'b28c4e96ad10'
down_revision = 'a6d307b912e4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('projects', sa.Column('architecture_version', sa.Integer(), nullable=False, server_default='1'))


def downgrade():
    raise RuntimeError('不支持把新工作区降级到旧执行架构，请恢复完整备份')
