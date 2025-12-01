"""add_output_format_to_paper_templates

Revision ID: 736361c89d6a
Revises: 906ca23fbfe1
Create Date: 2025-12-01 13:44:45.813991

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '736361c89d6a'
down_revision: Union[str, Sequence[str], None] = '906ca23fbfe1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('paper_templates', sa.Column('output_format', sa.String(length=10), nullable=False, server_default='markdown'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('paper_templates', 'output_format')
