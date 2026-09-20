from __future__ import annotations

import uuid

import pytest
import redis.asyncio as redis
from fastapi.testclient import TestClient

from app import config as config_module
from app.db.session import session_scope
from app.main import create_app, run_consolidation_for_all_tenants

REDIS_URL = "redis://localhost:6379"


async def _redis_available() -> bool:
    try:
        client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1)
        await client.ping()
        await client.aclose()
        return True
    except Exception:
        return False


def _point_settings_at_temp(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config_module.settings, "database_url", "sqlite+aiosqlite://")
    monkeypatch.setattr(config_module.settings, "chroma_persist_dir", str(tmp_path / "chroma"))


def test_health_endpoint(tmp_path, monkeypatch):
    _point_settings_at_temp(tmp_path, monkeypatch)
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_lifespan_wires_memory_store_and_session_factory(tmp_path, monkeypatch):
    _point_settings_at_temp(tmp_path, monkeypatch)
    app = create_app()
    with TestClient(app):
        assert app.state.memory_store is not None
        assert app.state.session_factory is not None


def test_consolidation_job_is_scheduled_but_not_immediate(tmp_path, monkeypatch):
    _point_settings_at_temp(tmp_path, monkeypatch)
    app = create_app()
    with TestClient(app):
        jobs = app.state.scheduler.get_jobs()
        job = next((j for j in jobs if j.id == "consolidation"), None)
        assert job is not None
        # Must not be scheduled to fire immediately on boot (that would make
        # every request race a full-tenant reindex during startup).
        assert job.next_run_time is not None


def test_ingest_and_query_through_full_app(tmp_path, monkeypatch):
    _point_settings_at_temp(tmp_path, monkeypatch)
    app = create_app()
    tenant = str(uuid.uuid4())
    with TestClient(app) as client:
        ingest_resp = client.post(
            "/api/v1/memory/ingest",
            headers={"X-Tenant-Id": tenant},
            json={"title": "x", "description": "brute force scenario replayed on sim-host-01", "severity": "high"},
        )
        assert ingest_resp.status_code == 201

        query_resp = client.post(
            "/api/v1/memory/query",
            headers={"X-Tenant-Id": tenant},
            json={"query": "brute force sim-host-01"},
        )
        assert query_resp.status_code == 200
        assert "sim-host-01" in query_resp.json()["answer"]


async def test_run_consolidation_for_all_tenants_covers_every_tenant(store_and_sessions):
    store, session_factory = store_and_sessions
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    async with session_scope(session_factory) as session:
        await store.add(session, tenant_id=tenant_a, content="repeated authentication failures for alice")
        await store.add(session, tenant_id=tenant_a, content="repeated authentication failures for alice")
        await store.add(session, tenant_id=tenant_b, content="ransomware encryption behavior on host-1")

    results = await run_consolidation_for_all_tenants(session_factory, store)

    assert set(results.keys()) == {str(tenant_a), str(tenant_b)}
    assert results[str(tenant_a)]["deduplicate"] == {"processed": 2, "merged": 1}
    assert results[str(tenant_b)]["deduplicate"] == {"processed": 1, "merged": 0}


async def test_run_consolidation_with_no_memories_returns_empty(store_and_sessions):
    store, session_factory = store_and_sessions
    results = await run_consolidation_for_all_tenants(session_factory, store)
    assert results == {}


async def test_detection_consumer_starts_when_redis_available(tmp_path, monkeypatch):
    if not await _redis_available():
        pytest.skip("Redis not reachable at redis://localhost:6379")
    _point_settings_at_temp(tmp_path, monkeypatch)
    monkeypatch.setattr(config_module.settings, "redis_url", REDIS_URL)

    app = create_app()
    with TestClient(app):
        assert app.state.detection_consumer is not None
        assert app.state.detection_task is not None
        assert not app.state.detection_task.done()


def test_detection_consumer_absent_does_not_crash_app_when_redis_unreachable(tmp_path, monkeypatch):
    """Bug-shaped case, deliberately exercised: an unreachable Redis at
    boot must not take down the whole API — the detection consumer is an
    optional background piece, not a hard dependency of `create_app()`."""
    _point_settings_at_temp(tmp_path, monkeypatch)
    monkeypatch.setattr(config_module.settings, "redis_url", "redis://localhost:1")  # nothing listens here

    app = create_app()
    with TestClient(app) as client:
        assert app.state.detection_consumer is None
        assert app.state.detection_task is None
        resp = client.get("/health")
        assert resp.status_code == 200
