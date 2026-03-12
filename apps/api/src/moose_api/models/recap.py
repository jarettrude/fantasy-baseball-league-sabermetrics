from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class Recap(Base):
    """AI-generated content recaps for leagues and teams.

    Stores generated recaps, briefings, and other AI content with metadata
    about the model used, token consumption, and cost. Stat payload stores
    the raw statistical context provided to the AI (redacted after 30 days).

    Status tracks the content lifecycle from draft through published.
    Type distinguishes between weekly recaps, manager briefings, etc.
    """

    __tablename__ = "recap"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(Integer, ForeignKey("league.id"))
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    team_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("team.id"), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="'draft'")
    stat_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
