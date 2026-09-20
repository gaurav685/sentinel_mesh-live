"""Minimal settings for SentinelMesh Live. Not a port of SentinelMesh's full
`AppSettings` (Kafka/OIDC/S3/etc config that doesn't apply here) — just what
this trimmed project actually needs so far.
"""

from __future__ import annotations

import logging

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SML_", env_file=".env", extra="ignore")

    # "development" (default) or "production". Purely a config-validation
    # switch (validate_production_config, below) -- never branches app
    # behavior itself, so it can't silently change what the app does.
    environment: str = "development"

    # Python stdlib logging level name, applied to the root logger at
    # startup (app/main.py).
    log_level: str = "INFO"

    # Comma-separated allowed origins for CORS, or "*" for the existing
    # documented wide-open default. app/main.py splits this into a list.
    # "*" remains the default because CORS is not this app's security
    # boundary yet (see app/memory/routes.py's get_tenant_id docstring) --
    # restricting it without a real auth layer behind it wouldn't add real
    # protection, just noise. Set explicitly once a verified-session layer
    # exists, or to silence validate_production_config's warning.
    cors_origins: str = "*"

    # Default: sqlite+aiosqlite so tests and local dev run with zero external
    # services. Production points this at Postgres (Railway/Render/Neon),
    # e.g. postgresql+asyncpg://user:pass@host/db.
    database_url: str = "sqlite+aiosqlite:///./data/sentinelmesh_live.db"

    # Local disk path for the embedded Chroma client. Treated as a
    # rebuildable cache (README: "Store substitutions") — Postgres is the
    # durable source of truth, this can be wiped and rebuilt.
    chroma_persist_dir: str = "./data/chroma"

    # How often the periodic consolidation job (dedup + pattern extraction,
    # app/main.py) runs, across every tenant found in Postgres.
    consolidation_interval_minutes: int = 15

    # Redis Streams connection, shared by app/bus/redis_streams.py and the
    # standalone `replay` process (README: "Process topology").
    redis_url: str = "redis://localhost:6379"

    # LLM for app/chat/query.py's incident chat. OpenAI-compatible
    # /chat/completions wire format over plain httpx -- not the `openai`
    # SDK -- so any provider implementing that format works by changing
    # these three values (OpenAI itself, Groq, Together, a local
    # Ollama/vLLM openai-compat server, ...).
    llm_api_base: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_s: float = 30.0
    llm_max_tokens: int = 1024

    # Max bytes accepted for any request body (Content-Length-based check,
    # app/main.py middleware) -- a public deployment has no other cap on
    # this today (Phase 7).
    max_request_body_bytes: int = 1_000_000

    # Per-client-IP requests/minute allowed against the LLM-calling routes
    # (`/api/chat`, chain report generation) -- caps LLM spend on a public
    # deployment. In-process only (no Redis-backed shared counter): correct
    # for this project's single-instance deployment target, not a
    # multi-replica-safe limiter -- see app/rate_limit.py.
    llm_rate_limit_per_minute: int = 20

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()

_log = logging.getLogger("sentinelmesh_live.config")


def validate_production_config(cfg: Settings) -> list[str]:
    """Returns a list of human-readable warnings for settings that are
    almost certainly wrong in `environment=production` -- e.g. a Postgres
    URL was never provided, so requests would silently hit an ephemeral
    per-container sqlite file. Deliberately returns warnings rather than
    raising: none of these make the app incapable of serving traffic (the
    existing architecture explicitly allows degraded operation), so failing
    startup over them would contradict Phase 2's "fail clearly only for
    mandatory variables" rule -- there is no *mandatory* variable here that
    the app cannot run without, only unsafe defaults left in place. Missing
    `SML_LLM_API_KEY` is intentionally NOT included: app/chat/llm.py already
    fails clearly, per-request, with `LLMUnavailable` -- that is the
    documented graceful-degradation path, not a startup-time concern.
    """
    warnings: list[str] = []
    if cfg.environment != "production":
        return warnings
    if cfg.database_url.startswith("sqlite"):
        warnings.append(
            "SML_DATABASE_URL is still the sqlite default in production -- "
            "data will not survive most container platforms' ephemeral "
            "filesystem. Set it to a hosted Postgres URL."
        )
    if "localhost" in cfg.redis_url or "127.0.0.1" in cfg.redis_url:
        warnings.append(
            "SML_REDIS_URL still points at localhost in production -- set "
            "it to the hosted Redis instance's URL."
        )
    if cfg.cors_origins.strip() == "*":
        warnings.append(
            "SML_CORS_ORIGINS is still '*' in production -- restrict it to "
            "the deployed frontend's real origin(s) once a verified-session "
            "auth layer exists (see app/memory/routes.py get_tenant_id)."
        )
    for warning in warnings:
        _log.warning("production_config_warning", extra={"detail": warning})
    return warnings


__all__ = ["Settings", "settings", "validate_production_config"]
