from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings


def make_engine(database_url: str | None = None) -> AsyncEngine:
    """Bug found running the real app under uvicorn (not caught by tests,
    which only ever used a bare `sqlite+aiosqlite://` in-memory URL):
    aiosqlite does not create missing parent directories for a file-based
    URL, so the default `./data/sentinelmesh_live.db` crashed
    `init_models()` on first boot with `unable to open database file`
    whenever `./data/` didn't already exist. `sqlite3`/`asyncpg` have the
    same non-issue for Postgres (the server, not this process, owns
    directory/file creation there) -- this only matters for sqlite."""
    url = database_url or settings.database_url
    parsed = make_url(url)
    is_sqlite = parsed.get_backend_name() == "sqlite"
    if is_sqlite and parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)

    # pool_pre_ping: hosted free-tier Postgres providers (Neon, Supabase,
    # Railway, ...) commonly close idle connections server-side without
    # telling this pool -- pre_ping issues a cheap SELECT 1 before handing
    # out a pooled connection so a dead one is transparently replaced
    # instead of surfacing as a request-time error. No effect on sqlite
    # (single-connection, nothing to go stale), so only set for real DBs.
    engine_kwargs: dict[str, object] = {"echo": False}
    if not is_sqlite:
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_recycle"] = 1800
    return create_async_engine(url, **engine_kwargs)


__all__ = ["make_engine"]
