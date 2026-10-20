"""merge counting lines into lines

Revision ID: d81673df6c15
Revises: 665f4e47a06d
Create Date: 2026-10-20 16:58:27.307307

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd81673df6c15'
down_revision: Union[str, Sequence[str], None] = '665f4e47a06d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Lines get an alert switch; count-only "counting_line" rules become lines with alerts off."""
    op.add_column("detection_rules", sa.Column("alert", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.execute("UPDATE detection_rules SET type = 'line_crossing', alert = false WHERE type = 'counting_line'")


def downgrade() -> None:
    op.execute("UPDATE detection_rules SET type = 'counting_line' WHERE type = 'line_crossing' AND alert = false")
    op.drop_column("detection_rules", "alert")
