"""Authentication and authorization schemas for API requests and responses.

Defines Pydantic models for OAuth flow, user authentication, and session
management with Yahoo Fantasy Sports API integration.
"""

from __future__ import annotations

from pydantic import BaseModel


class LoginResponse(BaseModel):
    """Response model for OAuth login initiation.

    Contains the Yahoo authorization URL that the client should redirect
    the user to for OAuth authentication.
    """

    redirect_url: str


class CallbackRequest(BaseModel):
    """Request model for OAuth callback processing.

    Contains the authorization code and state token returned by Yahoo
    after user authorization.
    """

    code: str
    state: str


class CallbackResponse(BaseModel):
    """Response model after successful OAuth callback processing.

    Contains user information and role after successful authentication
    and token exchange.
    """

    user_id: int
    display_name: str
    role: str


class UserResponse(BaseModel):
    """Response model for user profile information.

    Contains user details including role and briefing notification status.
    Used for session management and UI display.
    """

    id: int
    yahoo_guid: str
    display_name: str
    role: str
    has_unread_briefing: bool = False
