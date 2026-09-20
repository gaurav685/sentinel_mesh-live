"""SentinelMesh Live's `api` process — the FastAPI app free-tier hosting
collapses SentinelMesh's 19-service topology into (README: "Process
topology: 2 deployables, not 19").

What this wires together, since it's the only piece that actually exists
so far:
  - `app/memory/routes.py` — the memory layer's HTTP surface
  - a periodic consolidation job (APScheduler) across every tenant found
    in Postgres — this is the background-task half of `routes.py`'s
    stated gap #2 (`/consolidate` staying available as a manual trigger
    for demos, this job covering the "runs periodically" case for real)
  - Postgres (`init_models` — a stand-in for Alembic, see `db/session.py`)
    and the embedded Chroma client, both handed to routes via `app.state`

  - the detection consumer (`app/detection/consumer.py`) as a background
    task: consumes `telemetry.raw` (the gap the previous version of this
    docstring flagged — `replay/main.py` had nothing downstream), scores
    every event with the real trained isolation-forest artifact, persists
    to Postgres, and creates/links incident memories in the memory layer.
  - `app/chat/routes.py` — natural-language chat over the memory layer
    (`POST /api/chat`), grounded in retrieved incidents, never answering
    from nothing (see `app/chat/query.py`'s non-fabrication gate).

Started defensively, not with a hard dependency: if Redis is unreachable
or the model artifact is missing at boot, the exception is logged and the
API still starts and serves the memory layer normally — a demo running
without `docker`/Redis up shouldn't lose the whole app over an optional
background consumer. `app.state.detection_consumer`/`detection_task` are
`None` when this happens; check them, don't assume they're set.

What this still does NOT wire: the ingestion/normalization background
task and the correlation-engine background task (`events.canonical`,
attack-chain building) are not built. The detection consumer above is the
first real consumer of `telemetry.raw`; nothing yet consumes what it
would produce past a `detection` row and an incident memory.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import chromadb
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bus.redis_streams import EventBusConsumer
from app.chat.routes import router as chat_router
from app.config import settings, validate_production_config
from app.db.engine import make_engine
from app.db.memory_models import MemoryRecordRow
from app.db.session import init_models, make_session_factory, session_scope
from app.detection.consumer import start_detection_consumer
from app.detection.model import IsolationForestModel
from app.detection.routes import router as detection_router
from app.memory.consolidation import deduplicate, extract_patterns
from app.memory.embedder import TfidfEmbedder
from app.memory.routes import router as memory_router
from app.memory.store import MemoryStore

__all__ = ["create_app", "app", "run_consolidation_for_all_tenants"]

logging.basicConfig(level=settings.log_level.upper())
_log = logging.getLogger("sentinelmesh_live.main")


async def run_consolidation_for_all_tenants(
    session_factory: async_sessionmaker, store: MemoryStore
) -> dict[str, Any]:
    """Runs `deduplicate` + `extract_patterns` for every distinct tenant
    currently in Postgres. No tenant registry exists to enumerate tenants
    from (SentinelMesh's tenant table isn't ported here — `memory_models.py`'s
    docstring), so this discovers them directly from `memory_record`
    instead of assuming a fixed demo tenant."""
    async with session_scope(session_factory) as session:
        tenant_ids = (
            await session.execute(select(MemoryRecordRow.tenant_id).distinct())
        ).scalars().all()

    results: dict[str, Any] = {}
    for tenant_id in tenant_ids:
        async with session_scope(session_factory) as session:
            dedup_result = await deduplicate(store, session, tenant_id)
            patterns = await extract_patterns(store, session, tenant_id)
        results[str(tenant_id)] = {"deduplicate": dedup_result, "pattern_count": len(patterns)}
        _log.info(
            "consolidation_tenant_done",
            extra={"tenant_id": str(tenant_id), "merged": dedup_result["merged"], "patterns": len(patterns)},
        )
    return results


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    validate_production_config(settings)

    engine = make_engine()
    try:
        await init_models(engine)
    except Exception:
        _log.exception(
            "database_init_failed",
            extra={"detail": "could not connect to or initialize SML_DATABASE_URL; check the URL, "
                              "credentials, and that the host is reachable from this environment"},
        )
        raise
    session_factory = make_session_factory(engine)

    chroma_dir = Path(settings.chroma_persist_dir)
    try:
        chroma_dir.mkdir(parents=True, exist_ok=True)
        chroma_client = chromadb.PersistentClient(path=str(chroma_dir))
    except Exception:
        _log.exception(
            "chroma_init_failed",
            extra={"detail": f"could not create/open Chroma persist dir at {chroma_dir}; "
                              "check it is a writable, mounted path"},
        )
        raise
    embedder = TfidfEmbedder()
    store = MemoryStore(chroma_client, embedder)

    # Real bug, found live: Chroma persists its vectors to disk across
    # process restarts, but TfidfEmbedder's fitted vocabulary lives only
    # in this process's memory. A fresh boot's embedder is always
    # unfitted, even when Chroma already holds real data from a previous
    # run -- vector_search()'s `is_fitted` guard then silently returns []
    # for every query, with no error, until something happens to add a
    # new memory. `rebuild_if_empty` doesn't catch this: it only acts when
    # Chroma itself is empty, not when Chroma has data but the in-process
    # embedder doesn't. Fix: reindex every known tenant's corpus at boot,
    # unconditionally, so the embedder is always fit before serving
    # traffic. Stated limitation, not silently ignored: the embedder is
    # one instance shared across every tenant in this MemoryStore -- with
    # more than one tenant, each reindex_tenant() call re-fits it on just
    # that tenant's vocabulary, which invalidates the *other* tenants'
    # Chroma embeddings until they're reindexed too. Fine today (this
    # deployment has exactly one tenant); a real multi-tenant deployment
    # needs a per-tenant embedder/vocabulary, not a shared one.
    async with session_scope(session_factory) as session:
        tenant_ids = (await session.execute(select(MemoryRecordRow.tenant_id).distinct())).scalars().all()
        for tenant_id in tenant_ids:
            await store.reindex_tenant(session, tenant_id)

    app.state.session_factory = session_factory
    app.state.memory_store = store

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_consolidation_for_all_tenants,
        "interval",
        minutes=settings.consolidation_interval_minutes,
        args=[session_factory, store],
        id="consolidation",
        replace_existing=True,
    )
    scheduler.start()
    app.state.scheduler = scheduler

    detection_consumer: EventBusConsumer | None = None
    detection_task: asyncio.Task[None] | None = None
    detection_model: IsolationForestModel | None = None
    try:
        detection_consumer, detection_task, detection_model = await start_detection_consumer(
            redis_url=settings.redis_url, session_factory=session_factory, store=store
        )
    except Exception:
        _log.exception("detection_consumer_start_failed")
    app.state.detection_consumer = detection_consumer
    app.state.detection_task = detection_task
    app.state.detection_model = detection_model

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        if detection_consumer is not None:
            detection_consumer.request_stop()
            if detection_task is not None:
                try:
                    await asyncio.wait_for(detection_task, timeout=5.0)
                except Exception:
                    detection_task.cancel()
            await detection_consumer.stop()
        await engine.dispose()


def create_app() -> FastAPI:
    is_production = settings.environment == "production"
    app = FastAPI(
        title="SentinelMesh Live",
        lifespan=lifespan,
        # Phase 7: auto-generated docs/schema expose every route and model
        # shape to anyone; not a secret leak on their own, but unnecessary
        # public surface area once this is more than a local demo.
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )
    # Permissive by design, not an oversight: this is a local/single-tenant
    # demo with no verified-session boundary yet (routes.py's own stated
    # gap 1) -- CORS is not the security layer here, and restricting
    # origins wouldn't add real protection ahead of that gap being closed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def limit_request_body(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > settings.max_request_body_bytes:
            return JSONResponse(
                status_code=413,
                content={"error_code": "payload_too_large", "detail": "request body exceeds the configured limit"},
            )
        return await call_next(request)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Structured, secret-free response for anything that escapes a
        # route's own error handling -- HTTPException (used everywhere
        # else in this app for expected errors) is untouched by this
        # handler, Starlette dispatches those separately.
        _log.exception("unhandled_exception", extra={"path": str(request.url.path)})
        return JSONResponse(
            status_code=500,
            content={"error_code": "internal_error", "detail": "an unexpected error occurred"},
        )

    app.include_router(memory_router)
    app.include_router(chat_router)
    app.include_router(detection_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/live")
    async def live() -> dict[str, str]:
        # Liveness must never depend on external services (Phase 6) --
        # this process being able to answer HTTP at all is the whole check.
        return {"status": "alive"}

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        checks: dict[str, Any] = {}

        try:
            async with session_scope(request.app.state.session_factory) as session:
                await session.execute(select(1))
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {exc}"

        consumer: EventBusConsumer | None = getattr(request.app.state, "detection_consumer", None)
        if consumer is None:
            # Consistent with the rest of this app's degrade-gracefully
            # contract: an intentionally-absent consumer (Redis unreachable
            # at boot) is reported, not treated as a readiness failure --
            # the memory/chat routes serve traffic fine without it.
            checks["redis"] = "not started"
        else:
            try:
                await consumer.ping()
                checks["redis"] = "ok"
            except Exception as exc:
                checks["redis"] = f"error: {exc}"

        model: IsolationForestModel | None = getattr(request.app.state, "detection_model", None)
        checks["model_loaded"] = model is not None
        if model is not None:
            checks["model_version"] = model.model_version
            checks["model_framework_version"] = f"scikit-learn=={model.sklearn_version}"
            checks["model_artifact_checksum"] = model.artifact_checksum

        # Ready means "can serve the memory-layer API," which only
        # requires the database -- Redis/model are optional per this
        # app's own documented degrade-gracefully contract (app/main.py
        # lifespan docstring), so they're reported but never fail /ready.
        is_ready = checks["database"] == "ok"
        return JSONResponse(status_code=200 if is_ready else 503, content=checks)

    return app


app = create_app()
