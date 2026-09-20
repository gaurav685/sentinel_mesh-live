from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.session import session_scope
from app.detection.consumer import process_record
from app.detection.routes import router as detection_router
from app.memory.routes import router as memory_router
from tests.unit.test_detection_consumer import (
    _ROW_0_NORMAL,
    _ROW_30_APACHE2,
    _ROW_61_WAREZMASTER,
    _event,
    _record,
)

TENANT = uuid.uuid4()


@pytest.fixture
async def client(store_and_sessions):
    from app.detection.consumer import MODEL_DIR
    from app.detection.model import IsolationForestModel

    store, session_factory = store_and_sessions
    app = FastAPI()
    app.include_router(memory_router)
    app.include_router(detection_router)
    app.state.memory_store = store
    app.state.session_factory = session_factory

    model = IsolationForestModel.load(MODEL_DIR)
    async with session_scope(session_factory) as session:
        await process_record(
            _record(_event(_ROW_0_NORMAL, src_ip="10.50.0.0", tenant_id=TENANT), "0-1"),
            session_factory=session_factory, store=store, model=model,
        )
        await process_record(
            _record(_event(_ROW_30_APACHE2, src_ip="10.50.0.30", tenant_id=TENANT), "0-2"),
            session_factory=session_factory, store=store, model=model,
        )
        await process_record(
            _record(_event(_ROW_61_WAREZMASTER, src_ip="10.50.0.61", tenant_id=TENANT), "0-3"),
            session_factory=session_factory, store=store, model=model,
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers():
    return {"X-Tenant-Id": str(TENANT)}


async def test_list_detections_returns_all_three_newest_first(client):
    resp = await client.get("/api/v1/detections", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert [d["created_at"] for d in body] == sorted((d["created_at"] for d in body), reverse=True)
    anomaly_flags = {d["is_anomaly"] for d in body}
    assert anomaly_flags == {True, False}
    apache2 = next(d for d in body if d["raw_event"]["bytes_sent"] == 76944)
    assert apache2["is_anomaly"] is True
    assert apache2["memory_id"] is not None
    assert apache2["raw_event"]["protocol"] == "tcp"


async def test_stats_reflects_real_counts(client):
    resp = await client.get("/api/v1/stats", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_events"] == 3
    assert body["anomaly_count"] == 2
    assert body["memory_count"] == 2


async def test_memory_list_endpoint_returns_incidents_with_tags(client):
    resp = await client.get("/api/v1/memory", headers=_headers(), params={"memory_type": "incident"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    for item in body:
        assert item["memory_type"] == "incident"
        assert "tags" in item


async def test_list_detections_filters_by_memory_id(client):
    all_resp = await client.get("/api/v1/detections", headers=_headers())
    apache2 = next(d for d in all_resp.json() if d["raw_event"]["bytes_sent"] == 76944)

    resp = await client.get(
        "/api/v1/detections", headers=_headers(), params={"memory_id": apache2["memory_id"]}
    )
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == apache2["id"]


async def test_stats_isolated_per_tenant(client):
    other = str(uuid.uuid4())
    resp = await client.get("/api/v1/stats", headers={"X-Tenant-Id": other})
    body = resp.json()
    assert body == {"total_events": 0, "anomaly_count": 0, "memory_count": 0}


async def test_detections_have_different_techniques_and_computed_threat_scores(client):
    resp = await client.get("/api/v1/detections", headers=_headers())
    body = resp.json()

    apache2 = next(d for d in body if d["raw_event"]["bytes_sent"] == 76944)
    warezmaster = next(d for d in body if d["raw_event"]["bytes_sent"] == 283618)
    normal = next(d for d in body if d["is_anomaly"] is False)

    assert apache2["technique"] is not None
    assert warezmaster["technique"] is not None
    assert apache2["technique"]["id"] != warezmaster["technique"]["id"]
    assert normal["technique"] is None

    assert apache2["chain_length"] == 1  # standalone, not yet linked to anything
    assert 0.0 <= apache2["threat_score"] <= 1.0
    assert apache2["threat_score"] > normal["threat_score"]


async def test_chains_endpoint_empty_when_nothing_linked(client):
    resp = await client.get("/api/v1/chains", headers=_headers())
    assert resp.status_code == 200
    assert resp.json() == []


async def test_chain_report_404_for_unknown_chain(client):
    resp = await client.post("/api/v1/chains/does-not-exist/report", headers=_headers())
    assert resp.status_code == 404


@pytest.fixture
async def client_with_linked_pair(store_and_sessions):
    from app.detection.consumer import MODEL_DIR
    from app.detection.model import IsolationForestModel

    store, session_factory = store_and_sessions
    app = FastAPI()
    app.include_router(memory_router)
    app.include_router(detection_router)
    app.state.memory_store = store
    app.state.session_factory = session_factory

    model = IsolationForestModel.load(MODEL_DIR)
    async with session_scope(session_factory) as session:
        await process_record(
            _record(_event(_ROW_30_APACHE2, src_ip="10.50.0.30", tenant_id=TENANT), "0-1"),
            session_factory=session_factory, store=store, model=model,
        )
        await process_record(
            _record(_event(_ROW_30_APACHE2, src_ip="10.50.0.99", tenant_id=TENANT), "0-2"),
            session_factory=session_factory, store=store, model=model,
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_chains_endpoint_finds_real_linked_pair(client_with_linked_pair):
    resp = await client_with_linked_pair.get("/api/v1/chains", headers=_headers())
    assert resp.status_code == 200
    chains = resp.json()
    assert len(chains) == 1
    assert chains[0]["length"] == 2


async def test_chain_length_reflected_in_detections_threat_score(client_with_linked_pair):
    detections = (await client_with_linked_pair.get("/api/v1/detections", headers=_headers())).json()
    assert all(d["chain_length"] == 2 for d in detections)

    chains = (await client_with_linked_pair.get("/api/v1/chains", headers=_headers())).json()
    chain_id = chains[0]["chain_id"]

    chain_detail = await client_with_linked_pair.get(f"/api/v1/chains/{chain_id}", headers=_headers())
    assert chain_detail.status_code == 200
    assert chain_detail.json()["length"] == 2


async def test_chain_report_without_llm_key_returns_503(client_with_linked_pair, monkeypatch):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "llm_api_key", None)
    chains = (await client_with_linked_pair.get("/api/v1/chains", headers=_headers())).json()
    chain_id = chains[0]["chain_id"]

    resp = await client_with_linked_pair.post(f"/api/v1/chains/{chain_id}/report", headers=_headers())
    assert resp.status_code == 503


async def test_chain_report_with_mocked_llm_cites_real_incident_ids(client_with_linked_pair, monkeypatch):
    import httpx
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "llm_api_key", "test-key")

    chains = (await client_with_linked_pair.get("/api/v1/chains", headers=_headers())).json()
    chain_id = chains[0]["chain_id"]
    real_memory_ids = [m["id"] for m in chains[0]["memories"]]

    captured = {}
    real_post = httpx.AsyncClient.post

    async def fake_post(self, url, *args, **kwargs):
        if not str(url).endswith("/chat/completions"):
            return await real_post(self, url, *args, **kwargs)
        captured["json"] = kwargs.get("json")

        class FakeResponse:
            status_code = 200

            def json(self):
                narrative = " then ".join(f"incident {mid} occurred" for mid in real_memory_ids)
                return {"choices": [{"message": {"content": narrative}}]}

        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    resp = await client_with_linked_pair.post(f"/api/v1/chains/{chain_id}/report", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert all(mid in body["report"] for mid in real_memory_ids)
    assert "verify against raw incident data" in body["disclaimer"].lower()
    prompt_text = captured["json"]["messages"][1]["content"]
    assert all(mid in prompt_text for mid in real_memory_ids)
