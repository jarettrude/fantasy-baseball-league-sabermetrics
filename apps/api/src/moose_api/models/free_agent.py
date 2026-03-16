"""Free agent availability tracking model.

Records snapshots of player availability over time to track trends
in free agent pool composition and player movement between teams
and the waiver wire.
"""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class FreeAgentSnapshot(Base):
    """Snapshot of free agent availability at a point in time.

    Records which players were available as free agents for each league at
    specific timestamps. Supports trend analysis of player availability and
    helps identify when players were dropped or picked up.

    Snapshots older than 48 hours are purged per spec S5 to manage storage.
    """

    __tablename__ = "free_agent_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(Integer, ForeignKey("league.id"))
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("player.id"))
    snapshot_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
