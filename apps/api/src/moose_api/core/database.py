"""Database connection and session management.

Provides async SQLAlchemy engine configuration and dependency
injection for database sessions with automatic transaction handling.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from moose_api.core.config import settings

engine = create_async_engine(
    settings.effective_database_url,
    echo=settings.is_dev,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Database session dependency with automatic transaction management.

    Yields a database session with automatic commit on success
    and rollback on exception for reliable transaction handling.

    Yields:
        AsyncSession: Database session for use in endpoints
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
