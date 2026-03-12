"""Admin-related schemas for system management and monitoring.

Defines Pydantic models for notifications, job management, AI settings,
and administrative overview data.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    """Response model for commissioner notifications.

    Contains notification details including message, metadata, and
    read status for system alerts.
    """

    id: int
    type: str
    message: str
    metadata: dict
    is_read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    """Response model for notification list.

    Contains list of notifications with unread count.
    """

    notifications: list[NotificationResponse]
    unread_count: int


class SyncTriggerRequest(BaseModel):
    """Request model for triggering synchronization jobs.

    Contains job name for manual job execution.
    """

    job_name: str


class SyncTriggerResponse(BaseModel):
    """Response model for sync job trigger.

    Contains job status and message after triggering.
    """

    job_name: str
    status: str
    message: str


class AISettingsResponse(BaseModel):
    """Response model for AI configuration settings.

    Contains AI model configuration and prompt templates for
    content generation.
    """

    primary_model: str
    fallback_model: str
    league_recap_prompt: str
    manager_recap_prompt: str
    manager_briefing_prompt: str
    guardrails: str


class AISettingsUpdateRequest(BaseModel):
    """Request model for updating AI settings.

    Contains optional fields for updating AI prompts and guardrails.
    """

    league_recap_prompt: str | None = None
    manager_recap_prompt: str | None = None
    manager_briefing_prompt: str | None = None
    guardrails: str | None = None


class JobStatusResponse(BaseModel):
    """Response model for background job status.

    Contains job execution details including timing, status,
    and error information.
    """

    job_name: str
    last_success: datetime | None = None
    last_failure: datetime | None = None
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_seconds: float | None = None
    error: str | None = None


class AdminOverviewResponse(BaseModel):
    """Response model for admin dashboard overview.

    Contains system-wide statistics, job statuses, and notification
    counts for administrative monitoring.
    """

    unread_notifications: int
    job_statuses: list[JobStatusResponse]
    user_count: int = 0
    team_count: int = 0
    player_count: int = 0
    recap_count: int = 0
    job_stop_active: bool = False
    job_stop_since: datetime | None = None


class AdminBriefingResponse(BaseModel):
    """Response model for manager briefing in admin view.

    Contains briefing details including team, content, and view status.
    """

    id: int
    team_name: str
    date: date
    content: str
    is_viewed: bool
    created_at: datetime
