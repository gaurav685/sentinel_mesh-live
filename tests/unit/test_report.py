"""Same mocking posture as test_chat_query.py, for the same stated reason
(no free LLM credential in this environment): only the HTTP layer is
mocked. The non-fabrication contract this actually tests -- the prompt
contains only the real chain's real incidents, in real order -- is
checked directly against the captured request payload.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.chat.report import REPORT_DISCLAIMER, generate_chain_report
from app.chat.llm import LLMUnavailable
from app.db.memory_models import MemoryRecordRow
from app.detection.chains import Chain


def _fake_memory(content: str, when: datetime) -> MemoryRecordRow:
    row = MemoryRecordRow(
        tenant_id=uuid.uuid4(), content=content, memory_type="incident", importance_score=0.7
    )
    row.id = uuid.uuid4()
    row.created_at = when
    return row


async def test_generate_chain_report_prompt_contains_only_real_chain_incidents(monkeypatch):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "llm_api_key", "test-key")

    t0 = datetime.now(timezone.utc)
    first = _fake_memory("apache2-style anomaly on host-30, bytes_sent=76944", t0)
    second = _fake_memory("apache2-style anomaly on host-99, bytes_sent=76944", t0 + timedelta(minutes=1))
    chain = Chain(chain_id=str(first.id), memories=[first, second])

    captured = {}

    async def fake_post(self, url, *, headers, json):
        captured["json"] = json

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": f"First {first.id}, then {second.id}."}}]}

        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    report = await generate_chain_report(chain)

    assert str(first.id) in report
    assert str(second.id) in report
    prompt_text = captured["json"]["messages"][1]["content"]
    assert str(first.id) in prompt_text
    assert str(second.id) in prompt_text
    assert "apache2-style anomaly on host-30" in prompt_text
    assert "apache2-style anomaly on host-99" in prompt_text


async def test_generate_chain_report_no_key_raises(monkeypatch):
    from app import config as config_module

    monkeypatch.setattr(config_module.settings, "llm_api_key", None)
    chain = Chain(chain_id="x", memories=[_fake_memory("solo incident content here", datetime.now(timezone.utc))])

    with pytest.raises(LLMUnavailable):
        await generate_chain_report(chain)


def test_report_disclaimer_is_explicit():
    assert "LLM-generated" in REPORT_DISCLAIMER
    assert "verify" in REPORT_DISCLAIMER.lower()
