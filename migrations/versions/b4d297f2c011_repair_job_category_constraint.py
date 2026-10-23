"""repair job category constraint

Revision ID: b4d297f2c011
Revises: 4a491ab68807
Create Date: 2026-10-23 12:50:00

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d297f2c011"
down_revision: Union[str, Sequence[str], None] = "4a491ab68807"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_opp_category", "opportunities", type_="check")
    op.create_check_constraint(
        "ck_opp_category",
        "opportunities",
        "category IN ('admission','scholarship','internship','job')",
    )


def downgrade() -> None:
    pass
