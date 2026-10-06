from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from moose_api.core.config import settings
from moose_api.core.csrf import CSRFMiddleware
from moose_api.routers import admin, auth, league, players, recaps

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage application lifecycle and cleanup resources.

    Ensures database connections and Redis clients are properly closed
    when the application shuts down to prevent connection leaks.

    Args:
        _app: The FastAPI application instance (unused, required by FastAPI)
    """
    from moose_api.core.database import engine
    from moose_api.core.redis import redis_client

    yield

    await engine.dispose()
    await redis_client.aclose()


app = FastAPI(
    title="Moose Sports Empire API",
    description="Fantasy Baseball Companion API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(CSRFMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["*", "X-CSRF-Token"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(league.router, prefix="/api")
app.include_router(players.router, prefix="/api")
app.include_router(recaps.router, prefix="/api")
app.include_router(admin.router, prefix="/api")


@app.get("/api/health")
async def health():
    """Health check endpoint for monitoring and load balancer probes.

    Returns basic application status and demo mode flag for
    infrastructure health checks.

    Returns:
        Dict with status and demo_mode flag
    """
    return {"status": "ok", "demo_mode": settings.demo_mode}


@app.get("/api/config")
async def get_config():
    """Expose non-secret configuration to the frontend.

    Returns league branding, demo mode status, and environment settings
    for UI customization without exposing sensitive credentials.

    Returns:
        Dict containing league name, colors, demo mode, and app environment
    """
    return {
        "league_name": settings.league_name,
        "league_primary_color": settings.league_primary_color,
        "league_secondary_color": settings.league_secondary_color,
        "demo_mode": settings.demo_mode,
        "app_env": settings.app_env,
    }


@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception):
    """Catch-all exception handler for unhandled errors.

    Logs full exception details for debugging while returning a generic
    error message to clients to avoid leaking sensitive information.

    Args:
        _request: The incoming request (unused, required by FastAPI)
        exc: The unhandled exception

    Returns:
        JSONResponse with 500 status and generic error message
    """
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
