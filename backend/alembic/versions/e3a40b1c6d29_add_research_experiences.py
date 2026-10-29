"""Add owner-scoped research experience index and persistent tombstones."""
from alembic import op
import sqlalchemy as sa

revision='e3a40b1c6d29'
down_revision='c4e8a1b2d9f0'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('research_experiences',
        sa.Column('id',sa.String(64),primary_key=True),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('source_work_id',sa.String(50),nullable=False),
        sa.Column('source_key',sa.String(255),nullable=False,unique=True),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('content_hash',sa.String(64),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),
        sa.Column('enabled',sa.Boolean(),nullable=False),
        sa.Column('deleted',sa.Boolean(),nullable=False))
    op.create_index('ix_research_experiences_user_id','research_experiences',['user_id'])
    op.create_index('ix_research_experiences_source_work_id','research_experiences',['source_work_id'])
    op.create_table('research_experience_preferences',
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),primary_key=True),
        sa.Column('enabled',sa.Boolean(),nullable=False))


def downgrade():
    op.drop_table('research_experience_preferences')
    op.drop_index('ix_research_experiences_source_work_id',table_name='research_experiences')
    op.drop_index('ix_research_experiences_user_id',table_name='research_experiences')
    op.drop_table('research_experiences')
