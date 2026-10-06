"""add_pvs_latest_lookup_index

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-04-20 21:45:00.000000

Adds a covering index that matches the hot read pattern used by
``_get_player_value`` and several other readers: filter by ``player_id``
and ``type`` then take the most recent ``snapshot_date``. The existing
unique constraint indexes ``(player_id, snapshot_date, type)`` which
forces a filter-after-scan when the caller wants the latest row for a
specific type. Creating ``(player_id, type, snapshot_date DESC)`` lets
Postgres satisfy ``ORDER BY snapshot_date DESC LIMIT 1`` from the
index alone.

Uses ``CREATE INDEX CONCURRENTLY`` inside an autocommit block so the
migration is safe to apply in production without locking reads/writes
on ``player_value_snapshot``.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INDEX_NAME = "ix_pvs_player_type_date_desc"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
            "ON player_value_snapshot (player_id, type, snapshot_date DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
