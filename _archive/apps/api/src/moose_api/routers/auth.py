"""Authentication endpoints for Yahoo OAuth integration.

Handles OAuth flow with Yahoo Fantasy Sports API, user authentication,
role-based access control, and session management with CSRF protection.
Implements strict membership validation for league access control.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from moose_api.core.bootstrap import enqueue_post_bootstrap_jobs, mark_bootstrap_ready
from moose_api.core.config import settings
from moose_api.core.csrf import set_csrf_cookie
from moose_api.core.database import get_db
from moose_api.core.rate_limit import auth_rate_limit
from moose_api.core.redis import get_redis
from moose_api.core.security import create_access_token, encrypt_token, get_current_user
from moose_api.models.user import User
from moose_api.models.yahoo_token import YahooToken
from moose_api.schemas.auth import CallbackRequest, CallbackResponse, LoginResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])

YAHOO_AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
YAHOO_TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
YAHOO_USERINFO_URL = "https://api.login.yahoo.com/openid/v1/userinfo"


@router.post("/login", response_model=LoginResponse, dependencies=[Depends(auth_rate_limit)])
async def login():
    """Initiate Yahoo OAuth flow for user authentication.

    Generates a secure state token for CSRF protection and returns
    the Yahoo authorization URL with required OAuth parameters.

    Returns:
        Login response containing the Yahoo authorization URL
    """
    state = secrets.token_urlsafe(32)  # CSRF protection
    redis = await get_redis()
    await redis.setex(f"oauth_state:{state}", 600, "pending")

    params = urlencode(
        {
            "client_id": settings.yahoo_client_id,
            "redirect_uri": settings.yahoo_redirect_uri,
            "response_type": "code",
            "scope": "fspt-r openid email",
            "state": state,
        }
    )

    return LoginResponse(redirect_url=f"{YAHOO_AUTH_URL}?{params}")


@router.post("/callback", response_model=CallbackResponse, dependencies=[Depends(auth_rate_limit)])
async def callback(
    req: CallbackRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    redis = await get_redis()
    stored = await redis.get(f"oauth_state:{req.state}")
    if not stored:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state",
        )
    await redis.delete(f"oauth_state:{req.state}")

    import base64

    credentials = base64.b64encode(f"{settings.yahoo_client_id}:{settings.yahoo_client_secret}".encode()).decode()

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            YAHOO_TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": req.code,
                "redirect_uri": settings.yahoo_redirect_uri,
            },
        )

    if token_resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Yahoo token exchange failed: {token_resp.text}",
        )

    token_data = token_resp.json()
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    yahoo_guid = token_data.get("xoauth_yahoo_guid", "")

    display_name = ""
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            YAHOO_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if userinfo_resp.status_code == 200:
        userinfo = userinfo_resp.json()
        if not yahoo_guid:
            yahoo_guid = userinfo.get("sub", "")
        display_name = userinfo.get("name", "") or userinfo.get("nickname", "")

    if not yahoo_guid:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not retrieve Yahoo GUID",
        )

    is_commissioner = yahoo_guid == settings.commissioner_yahoo_guid

    from moose_api.models.league import League
    from moose_api.models.team import Team

    league_check = await db.execute(select(League).limit(1))
    league = league_check.scalar_one_or_none()

    if is_commissioner:
        # Commissioner always allowed, trigger sync to verify League ID
        role = "commissioner"
    else:
        # Manager lock: reject if commissioner hasn't initialized league
        if not league:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="App initialization in progress. Please wait for the Commissioner to log in first.",
            )

        # Membership lock: must have a team in this specific league
        membership_check = await db.execute(
            select(Team).where(Team.yahoo_manager_guid == yahoo_guid, Team.league_id == league.id)
        )
        member_team = membership_check.scalar_one_or_none()
        if not member_team:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: You are not a manager in league {settings.yahoo_league_id}.",
            )
        role = "manager"

    result = await db.execute(select(User).where(User.yahoo_guid == yahoo_guid))
    user = result.scalar_one_or_none()
    resolved_display_name = display_name or (user.display_name if user and user.display_name else yahoo_guid)

    if user is None:
        user = User(
            yahoo_guid=yahoo_guid,
            display_name=resolved_display_name,
            role=role,
            last_login=datetime.now(UTC),
        )
        db.add(user)
        await db.flush()
    else:
        user.role = role
        user.display_name = resolved_display_name
        user.last_login = datetime.now(UTC)
        await db.flush()

    if role == "manager":
        member_team.manager_user_id = user.id
        await db.flush()

    existing_token = await db.execute(select(YahooToken).where(YahooToken.user_id == user.id))
    yahoo_token = existing_token.scalar_one_or_none()

    encrypted_access = encrypt_token(access_token)
    encrypted_refresh = encrypt_token(refresh_token)

    if yahoo_token is None:
        yahoo_token = YahooToken(
            user_id=user.id,
            access_token_encrypted=encrypted_access,
            refresh_token_encrypted=encrypted_refresh,
        )
        db.add(yahoo_token)
    else:
        yahoo_token.access_token_encrypted = encrypted_access
        yahoo_token.refresh_token_encrypted = encrypted_refresh
        yahoo_token.updated_at = datetime.now(UTC)

    jwt_token = create_access_token({"sub": str(user.id), "role": role})

    response.set_cookie(
        key="session_token",
        value=jwt_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.jwt_expiration_hours * 3600,
        path="/",
    )

    set_csrf_cookie(response)

    # On first commissioner login: mark system ready and enqueue preseason setup
    if role == "commissioner" and await mark_bootstrap_ready():
        await enqueue_post_bootstrap_jobs()

    team_result = await db.execute(select(Team).where(Team.manager_user_id == user.id))
    team = team_result.scalar_one_or_none()

    final_display_name = team.name if team else user.display_name

    return CallbackResponse(
        user_id=user.id,
        display_name=final_display_name,
        role=user.role,
    )


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("session_token", path="/")
    return {"message": "Logged out"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserResponse(**current_user)
