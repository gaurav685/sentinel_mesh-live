"""Unlike every other external dependency in this build (Redis, Postgres,
Chroma), the LLM API requires a paid credential this environment doesn't
have. The non-fabrication gate (no LLM call when nothing relevant was
retrieved) and the missing-key error path are tested for real, with no
mocking. The success path (an LLM actually composing a grounded answer)
mocks only the HTTP layer -- the real, non-fabrication proof for that path
is the live curl demonstration in the README, run against a real provider
with a real key.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.chat.query import LLMUnavailable, answer_chat
from app.db.session import session_scope

TENANT = uuid.uuid4()


async def test_no_relevant_memories_returns_explicit_no_match_without_calling_llm(
    store_and_sessions, monkeypatch
):
    store, session_factory = store_and_sessions

    called = False

    async def fake_post(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("LLM must not be called when nothing relevant was retrieved")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    async with session_scope(session_factory) as session:
        result = await answer_chat(store, session, "have we seen a smurf attack?", tenant_id=TENANT)

    assert called is False
    assert result["matches"] == []
    assert "No matching incidents" in result["answer"]
    assert result["grounded"] is True


async def test_empty_store_returns_no_match(store_and_sessions):
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        result = await answer_chat(store, session, "anything at all", tenant_id=TENANT)
    assert result["matches"] == []


async def test_relevant_memory_but_no_api_key_raises(store_and_sessions, monkeypatch):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "llm_api_key", None)
    store, session_factory = store_and_sessions

    async with session_scope(session_factory) as session:
        await store.add(
            session, tenant_id=TENANT, content="isolation forest anomaly on host-42", memory_type="incident"
        )

    async with session_scope(session_factory) as session:
        with pytest.raises(LLMUnavailable, match="no LLM API key"):
            await answer_chat(store, session, "isolation forest anomaly host-42", tenant_id=TENANT)


async def test_relevant_memory_composes_answer_via_mocked_llm(store_and_sessions, monkeypatch):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "llm_api_key", "test-key")
    store, session_factory = store_and_sessions

    async with session_scope(session_factory) as session:
        row = await store.add(
            session,
            tenant_id=TENANT,
            content="Anomalous network_flow activity from 10.50.0.30 (apache2-style DoS).",
            memory_type="incident",
            importance=0.76,
        )

    captured_payload = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": f"Yes, see incident {row.id}."}}]}

        @property
        def text(self):
            return ""

    async def fake_post(self, url, *, headers, json):
        captured_payload["url"] = url
        captured_payload["json"] = json
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    async with session_scope(session_factory) as session:
        result = await answer_chat(store, session, "apache2 anomaly on host-30", tenant_id=TENANT)

    assert str(row.id) in result["answer"]
    assert len(result["matches"]) == 1
    assert result["matches"][0]["memory_id"] == str(row.id)
    assert captured_payload["url"].endswith("/chat/completions")
    assert str(row.id) in captured_payload["json"]["messages"][1]["content"]  # context includes the real id


async def test_llm_http_error_raises_llm_unavailable(store_and_sessions, monkeypatch):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "llm_api_key", "test-key")
    store, session_factory = store_and_sessions

    async with session_scope(session_factory) as session:
        await store.add(session, tenant_id=TENANT, content="ransomware behavior on host-7", memory_type="incident")

    class FakeErrorResponse:
        status_code = 500
        text = "internal error"

    async def fake_post(self, url, *, headers, json):
        return FakeErrorResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    async with session_scope(session_factory) as session:
        with pytest.raises(LLMUnavailable, match="HTTP 500"):
            await answer_chat(store, session, "ransomware host-7", tenant_id=TENANT)
