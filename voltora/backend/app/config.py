"""Runtime configuration. Every value comes from VOLTORA_* environment variables or backend/.env."""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEV_SECRET = "dev-insecure-secret-change-me"
CORE_VERSION = "2.0.0-alpha.1"
HOST_API_VERSION = "1.0.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VOLTORA_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = "postgresql+psycopg://voltora@127.0.0.1:5433/voltora"
    secret_key: str = INSECURE_DEV_SECRET
    vault_key: str = ""
    public_url: str = "http://localhost:5173"
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000"]
    data_dir: Path = Path(".data")
    # Payments/couriers never reach real providers unless explicitly switched to live.
    payments_mode: Literal["sandbox", "live"] = "sandbox"
    external_network: bool = False
    ai_allow_private_endpoints: bool = False
    session_days: int = 14
    run_worker_in_process: bool = True
    trusted_proxies: list[str] = ["127.0.0.1"]
    log_level: str = "INFO"

    @field_validator("allowed_origins", "trusted_proxies", mode="before")
    @classmethod
    def _split(cls, v):
        if isinstance(v, str) and not v.startswith("["):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @model_validator(mode="after")
    def _production_guards(self):
        if self.env in ("staging", "production"):
            if self.secret_key == INSECURE_DEV_SECRET or len(self.secret_key) < 32:
                raise ValueError("VOLTORA_SECRET_KEY must be set to >=32 random chars outside development")
            if not self.vault_key:
                raise ValueError("VOLTORA_VAULT_KEY must be set outside development")
        return self

    @property
    def cookie_secure(self) -> bool:
        return self.env in ("staging", "production") or self.public_url.startswith("https://")


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()
