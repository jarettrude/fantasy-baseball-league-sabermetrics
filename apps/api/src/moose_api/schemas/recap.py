"""AI-generated recap schemas for league and team content.

Defines Pydantic models for AI-generated recap content, editing,
and regeneration requests with cost tracking.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class RecapResponse(BaseModel):
    """Response model for AI-generated recap content.

    Contains recap text, metadata about AI model usage, tokens,
    cost tracking, and publication status.
    """

    id: int
    week: int
    type: str
    team_id: int | None
    team_name: str | None
    status: str
    content: str | None
    model_used: str | None
    provider_used: str | None
    tokens_used: int | None
    cost_usd: Decimal | None
    published_at: datetime | None
    created_at: datetime


class RecapListResponse(BaseModel):
    """Response model for list of recaps.

    Contains multiple recap entries for a specific week.
    """

    recaps: list[RecapResponse]
    week: int


class RecapEditRequest(BaseModel):
    """Request model for editing recap content.

    Contains updated content for manual recap editing.
    """

    content: str


class RecapRegenerateRequest(BaseModel):
    """Request model for regenerating recap content.

    Contains parameters for recap regeneration including week,
    type, and optional team ID.
    """

    week: int
    type: str = "league"
    team_id: int | None = None
