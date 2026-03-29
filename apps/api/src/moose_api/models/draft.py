"""Draft pick and summary models for fantasy draft tracking.

Stores individual draft picks with pick order and team attribution,
plus AI-generated draft summary analysis.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class DraftPick(Base):
    """Individual draft pick record for a fantasy league draft.

    Captures each selection in pick order with team attribution and player
    reference. Supports full draft reconstruction for analysis and AI summary
    generation. Pick number is the global pick order (1-based) across all rounds.
    """

    __tablename__ = "draft_pick"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(Integer, ForeignKey("league.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("team.id"), nullable=False)
    player_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("player.id"), nullable=True)
    pick_number: Mapped[int] = mapped_column(Integer, nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    round_pick: Mapped[int] = mapped_column(Integer, nullable=False)
    player_name: Mapped[str] = mapped_column(Text, nullable=False)
    player_position: Mapped[str | None] = mapped_column(Text, nullable=True)
    yahoo_player_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())


class DraftSummary(Base):
    """AI-generated analysis of the full fantasy league draft.

    Stores a single AI-generated narrative summarizing draft results across all
    teams: strongest/weakest rosters, sleeper picks, busts, and notable players
    left on the board. Includes the data payload used for generation and metadata
    about the AI model and cost.
    """

    __tablename__ = "draft_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(Integer, ForeignKey("league.id"), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa.text("'draft'"))
    stat_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
