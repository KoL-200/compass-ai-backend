"""restore eligibility and strategy tables

Revision ID: 9c4a622d1c18
Revises: 44f73c4935bb
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "9c4a622d1c18"
down_revision: Union[str, Sequence[str], None] = "44f73c4935bb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eligibility_results",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("ruleset_version", sa.Integer(), nullable=False),
        sa.Column("engine_version", sa.String(length=20), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('eligible','likely_eligible','potentially_eligible','ineligible',"
            "'insufficient_information','needs_manual_verification')",
            name="ck_eligibility_status",
        ),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "opportunity_id", name="uq_eligibility_user_opportunity"),
    )
    op.create_index("ix_eligibility_user_status", "eligibility_results", ["user_id", "status"])

    op.create_table(
        "strategies",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=10), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("source", sa.String(length=5), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "warnings", postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('draft','accepted','rejected','superseded')", name="ck_strategy_status"),
        sa.CheckConstraint("source IN ('ai','rules')", name="ck_strategy_source"),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["ai_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategies_user_opportunity", "strategies", ["user_id", "opportunity_id"])
    op.create_index(
        "uq_strategy_one_accepted",
        "strategies",
        ["user_id", "opportunity_id"],
        unique=True,
        postgresql_where=sa.text("status = 'accepted'"),
    )

    op.create_table(
        "strategy_tasks",
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("detail", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("kind", sa.String(length=25), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("effort_days", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(length=10), server_default=sa.text("'todo'"), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('complete_profile','confirm_requirement','learn_skill','gather_documents',"
            "'prepare_materials','submit','follow_up','custom')",
            name="ck_strategy_task_kind",
        ),
        sa.CheckConstraint("status IN ('todo','done','skipped')", name="ck_strategy_task_status"),
        sa.CheckConstraint("origin IN ('ai','rules','user')", name="ck_strategy_task_origin"),
        sa.CheckConstraint("effort_days BETWEEN 1 AND 30", name="ck_strategy_task_effort"),
        sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_strategy_tasks_strategy_id", "strategy_tasks", ["strategy_id"])


def downgrade() -> None:
    op.drop_index("ix_strategy_tasks_strategy_id", table_name="strategy_tasks")
    op.drop_table("strategy_tasks")
    op.drop_index("uq_strategy_one_accepted", table_name="strategies")
    op.drop_index("ix_strategies_user_opportunity", table_name="strategies")
    op.drop_table("strategies")
    op.drop_index("ix_eligibility_user_status", table_name="eligibility_results")
    op.drop_table("eligibility_results")