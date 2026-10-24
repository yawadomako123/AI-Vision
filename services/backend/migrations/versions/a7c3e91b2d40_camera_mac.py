"""camera hardware address (finds cameras again after an IP change)

Revision ID: a7c3e91b2d40
Revises: ce51c392fdfd
Create Date: 2026-10-24 14:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e91b2d40'
down_revision: Union[str, Sequence[str], None] = 'ce51c392fdfd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('cameras') as batch_op:
        batch_op.add_column(sa.Column('mac', sa.String(length=17), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('cameras') as batch_op:
        batch_op.drop_column('mac')
