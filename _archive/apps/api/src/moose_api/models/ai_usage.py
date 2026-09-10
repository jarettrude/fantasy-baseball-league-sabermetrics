"""AI usage tracking model for monitoring and cost management.

Stores records of AI API calls including model usage, token counts,
and associated costs for budget tracking and analytics.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class AIUsageLog(Base):
    """Detailed logging of AI API usage for cost tracking and debugging.

    Records token consumption, costs, and success/failure status for all
    AI API calls. Links to recap entries to associate costs with generated content.

    Entries older than 30 days are purged per spec S14 to manage storage and
    comply with data retention policies.
    """

    __tablename__ = "ai_usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    recap_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("recap.id"), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
