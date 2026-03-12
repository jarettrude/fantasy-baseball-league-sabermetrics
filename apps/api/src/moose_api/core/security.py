"""Security utilities for JWT tokens, encryption, and authentication.

Provides JWT token creation/validation, token encryption for secure storage,
CSRF token generation, and user authentication dependencies with role-based
access control.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet
from fastapi import Cookie, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from moose_api.core.config import settings
from moose_api.core.database import get_db


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create JWT access token with configurable expiration.

    Encodes user data into a JWT with expiration claim for secure
    session management and authentication.

    Args:
        data: Payload data to encode (typically user ID and role)
        expires_delta: Optional custom expiration duration

    Returns:
        Signed JWT token string
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(hours=settings.jwt_expiration_hours))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate JWT access token.

    Verifies token signature and expiration, returning the payload
    if valid or raising HTTP exception for authentication errors.

    Args:
        token: JWT token string to decode

    Returns:
        Decoded token payload

    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from e


def encrypt_token(plaintext: str) -> str:
    """Encrypt sensitive token data using Fernet symmetric encryption.

    Used to securely store OAuth tokens in the database with
    encryption at rest for security compliance.

    Args:
        plaintext: Token string to encrypt

    Returns:
        Base64-encoded encrypted token
    """
    f = Fernet(settings.fernet_key.encode())
    return f.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt encrypted token data using Fernet symmetric encryption.

    Recovers original token from encrypted database storage.

    Args:
        ciphertext: Base64-encoded encrypted token

    Returns:
        Decrypted plaintext token

    Raises:
        InvalidToken: If decryption fails (invalid key or corrupted data)
    """
    f = Fernet(settings.fernet_key.encode())
    return f.decrypt(ciphertext.encode()).decode()


def generate_csrf_token() -> str:
    """Generate cryptographically secure CSRF token.

    Creates a random token for Cross-Site Request Forgery protection
    in state-changing operations.

    Returns:
        URL-safe random token string
    """
    return secrets.token_urlsafe(32)


async def get_current_user(
    request: Request,
    session_token: str | None = Cookie(None, alias="session_token"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_access_token(session_token)
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    from moose_api.models.team import Team
    from moose_api.models.user import User

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    team_result = await db.execute(select(Team).where(Team.manager_user_id == user.id))
    team = team_result.scalar_one_or_none()

    display_name = team.name if team else user.display_name

    has_unread_briefing = False
    if team:
        from moose_api.models.manager_briefing import ManagerBriefing

        briefing_result = await db.execute(
            select(ManagerBriefing)
            .where(ManagerBriefing.team_id == team.id, ManagerBriefing.is_viewed.is_(False))
            .limit(1)
        )
        has_unread_briefing = briefing_result.scalar_one_or_none() is not None

    return {
        "id": user.id,
        "yahoo_guid": user.yahoo_guid,
        "display_name": display_name,
        "role": user.role,
        "has_unread_briefing": has_unread_briefing,
    }


async def require_commissioner(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    if current_user["role"] != "commissioner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Commissioner access required",
        )
    return current_user
