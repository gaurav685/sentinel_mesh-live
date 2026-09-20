from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.memory.routes import router as memory_router
from app.chat.routes import router as chat_router

TENANT = uuid.uuid4()


@pytest.fixture
async def client(store_and_sessions):
    store, session_factory = store_and_sessions
    app = FastAPI()
    app.include_router(memory_router)
    app.include_router(chat_router)
    app.state.memory_store = store
    app.state.session_factory = session_factory

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers():
    return {"X-Tenant-Id": str(TENANT)}


async def test_chat_no_match_returns_explicit_no_hit(client):
    resp = await client.post("/api/chat", headers=_headers(), json={"query": "have we seen a smurf attack?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matches"] == []
    assert "No matching incidents" in body["answer"]


async def test_chat_missing_api_key_returns_503(client, monkeypatch):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "llm_api_key", None)
    await client.post(
        "/api/v1/memory/ingest",
        headers=_headers(),
        json={"title": "x", "description": "isolation forest anomaly on host-42", "severity": "high"},
    )

    resp = await client.post("/api/chat", headers=_headers(), json={"query": "anomaly on host-42"})
    assert resp.status_code == 503


async def test_chat_composes_grounded_answer_via_mocked_llm(client, monkeypatch):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "llm_api_key", "test-key")
    ingest_resp = await client.post(
        "/api/v1/memory/ingest",
        headers=_headers(),
        json={"title": "x", "description": "apache2-style DoS on host-30", "severity": "high"},
    )
    memory_id = ingest_resp.json()["memory_id"]

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": f"Yes, see incident {memory_id}."}}]}

        text = ""

    # Only intercept the LLM call -- `httpx.AsyncClient.post` is patched at
    # the class level, so it would otherwise also hijack this test's own
    # ASGI-transport client talking to the app itself.
    real_post = httpx.AsyncClient.post

    async def fake_post(self, url, *args, **kwargs):
        if str(url).endswith("/chat/completions"):
            return FakeResponse()
        return await real_post(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    resp = await client.post("/api/chat", headers=_headers(), json={"query": "apache2 DoS host-30"})
    assert resp.status_code == 200
    body = resp.json()
    assert memory_id in body["answer"]
    assert body["matches"][0]["memory_id"] == memory_id
