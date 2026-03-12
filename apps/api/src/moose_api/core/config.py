"""Application configuration with environment variable support.

Manages all application settings including database connections,
security keys, API credentials, and league configuration with
demo mode support and secret management.
"""

from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized application configuration.

    Loads settings from environment variables and secrets directory
    with validation, whitespace stripping, and demo mode support.
    """

    model_config = SettingsConfigDict(
        secrets_dir="/run/secrets",
        extra="ignore",
    )

    yahoo_client_id: str = ""
    yahoo_client_secret: str = ""
    yahoo_league_id: str = ""
    yahoo_redirect_uri: str = "https://localhost/callback"

    commissioner_yahoo_guid: str = ""

    db_user: str = "moose"
    db_password: str = "moose"
    database_url: str = "postgresql+asyncpg://moose:moose@db:5432/moose_empire"
    demo_database_url: str = "postgresql+asyncpg://moose:moose@db:5432/moose_demo"
    redis_url: str = "redis://redis:6379/0"

    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 72
    fernet_key: str = ""
    session_secret: str = ""
    csrf_secret: str = ""

    google_gemini_api_key: str = ""
    openrouter_api_key: str = ""
    the_odds_api_key: str = ""

    app_env: str = "development"
    demo_mode: bool = False
    local_timezone: str = "America/Vancouver"
    recap_generation_time: str = "10:00"
    web_origin: str = "https://localhost"
    log_level: str = "INFO"

    league_name: str = "Moose Sports Empire"
    league_season: int = 2026
    league_primary_color: str = "#8B1A1A"
    league_secondary_color: str = "#2D5016"

    @model_validator(mode="after")
    def _strip_secret_whitespace(self) -> Settings:
        """Remove trailing whitespace from string configuration values.

        Prevents configuration issues from secrets files with
        accidental whitespace or newline characters.
        """
        for field_name in self.model_fields:
            value = getattr(self, field_name)
            if isinstance(value, str):
                object.__setattr__(self, field_name, value.strip())
        return self

    @property
    def is_dev(self) -> bool:
        """Check if running in development environment."""
        return self.app_env == "development"

    @property
    def effective_database_url(self) -> str:
        """Get database URL with proper credentials and demo mode support.

        Returns demo database URL when demo_mode is enabled,
        otherwise returns production database with configured credentials.
        """
        from sqlalchemy.engine.url import make_url

        base_url = self.demo_database_url if self.demo_mode else self.database_url
        url = make_url(base_url)
        return url.set(
            username=self.db_user,
            password=self.db_password.strip(),
        ).render_as_string(hide_password=False)


settings = Settings()
