"""Zentrale Konfiguration aus Umgebungsvariablen (.env).

Alle Zugangsdaten werden ausschließlich über die .env-Datei bzw. verschlüsselt in der
Datenbank (Systemeinstellungen) gepflegt. Es gibt keine fest codierten Zugangsdaten.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- STRATO MySQL -------------------------------------------------------
    db_host: str = Field("", alias="DB_HOST")
    db_port: int = Field(3306, alias="DB_PORT")
    db_name: str = Field("", alias="DB_NAME")
    db_user: str = Field("", alias="DB_USER")
    db_password: str = Field("", alias="DB_PASSWORD")
    # disabled | preferred | required | verify_ca | verify_identity
    db_ssl_mode: str = Field("preferred", alias="DB_SSL_MODE")
    db_ssl_ca: Optional[str] = Field(None, alias="DB_SSL_CA")
    db_pool_size: int = Field(5, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(5, alias="DB_MAX_OVERFLOW")
    db_pool_recycle: int = Field(280, alias="DB_POOL_RECYCLE")
    db_connect_timeout: int = Field(10, alias="DB_CONNECT_TIMEOUT")

    # --- Anwendung ------------------------------------------------------------
    app_secret_key: str = Field("", alias="APP_SECRET_KEY")
    app_base_url: str = Field("", alias="APP_BASE_URL")  # z. B. https://wyse.tailnet.ts.net
    session_hours: int = Field(12, alias="SESSION_HOURS")
    cookie_secure: bool = Field(True, alias="COOKIE_SECURE")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    env_file_path: str = Field("/app/config/.env", alias="ENV_FILE_PATH")
    run_scheduler: bool = Field(True, alias="RUN_SCHEDULER")

    # --- Discord ---------------------------------------------------------------
    discord_bot_token: str = Field("", alias="DISCORD_BOT_TOKEN")
    discord_client_id: str = Field("", alias="DISCORD_CLIENT_ID")
    discord_client_secret: str = Field("", alias="DISCORD_CLIENT_SECRET")
    discord_guild_id: str = Field("", alias="DISCORD_GUILD_ID")
    discord_redirect_uri: str = Field("", alias="DISCORD_REDIRECT_URI")
    owner_discord_id: str = Field("", alias="OWNER_DISCORD_ID")
    discord_api_base: str = Field("https://discord.com/api/v10", alias="DISCORD_API_BASE")

    def missing_db_fields(self) -> list[str]:
        missing = []
        for env, value in (("DB_HOST", self.db_host), ("DB_NAME", self.db_name), ("DB_USER", self.db_user)):
            if not value:
                missing.append(env)
        return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def reset_settings_cache() -> None:
    get_settings.cache_clear()
