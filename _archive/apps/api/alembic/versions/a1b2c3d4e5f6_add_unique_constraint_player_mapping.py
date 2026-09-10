"""add_unique_constraint_player_mapping

Revision ID: a1b2c3d4e5f6
Revises: 56aba76fca58
Create Date: 2026-03-15 12:00:00.000000

Deduplicates existing player_mapping rows (keeping highest confidence per
yahoo_player_key) then adds a unique constraint so duplicates can never
re-accumulate.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "56aba76fca58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add unique constraint to player_mapping and deduplicate existing rows.

    Removes duplicate player mappings, keeping the entry with highest confidence
    score (and highest ID as tiebreaker) for each yahoo_player_key, then adds
    a unique constraint to prevent future duplicates.
    """
    conn = op.get_bind()

    # Delete duplicate player_mapping rows, keeping the one with the highest
    # source_confidence (and highest id as tiebreaker) for each yahoo_player_key.
    conn.execute(
        sa.text("""
            DELETE FROM player_mapping
            WHERE id NOT IN (
                SELECT DISTINCT ON (yahoo_player_key) id
                FROM player_mapping
                ORDER BY yahoo_player_key, source_confidence DESC, id DESC
            )
        """)
    )

    op.create_unique_constraint(
        "uq_player_mapping_yahoo_key",
        "player_mapping",
        ["yahoo_player_key"],
    )


def downgrade() -> None:
    """Remove the unique constraint to allow duplicate mappings again.

    Reverts the schema change but does not restore any deduplicated rows.
    """
    op.drop_constraint("uq_player_mapping_yahoo_key", "player_mapping", type_="unique")
