"""add_draft_pick_and_draft_summary

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-03-15 17:00:00.000000

Adds draft_pick table for storing individual draft selections in order,
and draft_summary table for persisting AI-generated draft analysis.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "draft_pick",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.Integer(), sa.ForeignKey("league.id"), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("team.id"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("player.id"), nullable=True),
        sa.Column("pick_number", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("round_pick", sa.Integer(), nullable=False),
        sa.Column("player_name", sa.Text(), nullable=False),
        sa.Column("player_position", sa.Text(), nullable=True),
        sa.Column("yahoo_player_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_draft_pick_league_id", "draft_pick", ["league_id"])
    op.create_index("ix_draft_pick_team_id", "draft_pick", ["team_id"])
    op.create_index("ix_draft_pick_pick_number", "draft_pick", ["pick_number"])

    op.create_table(
        "draft_summary",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_id", sa.Integer(), sa.ForeignKey("league.id"), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="'draft'"),
        sa.Column(
            "stat_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column("provider_used", sa.Text(), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(8, 6), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_draft_summary_league_id", "draft_summary", ["league_id"])
    op.create_index("ix_draft_summary_season", "draft_summary", ["season"])


def downgrade() -> None:
    op.drop_index("ix_draft_summary_season", "draft_summary")
    op.drop_index("ix_draft_summary_league_id", "draft_summary")
    op.drop_table("draft_summary")

    op.drop_index("ix_draft_pick_pick_number", "draft_pick")
    op.drop_index("ix_draft_pick_team_id", "draft_pick")
    op.drop_index("ix_draft_pick_league_id", "draft_pick")
    op.drop_table("draft_pick")
