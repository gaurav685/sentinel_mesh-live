"""Unit tests use a schema-shaped synthetic row (43 columns, only the
fields the mapper actually reads are non-zero) rather than transcribing a
real NSL-KDD line by hand -- avoids a transcription error silently
weakening the test. The end-to-end test below uses the real dataset file.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
import redis.asyncio as redis

from app.bus.redis_streams import EventBusConsumer
from replay.main import DEFAULT_DEMO_TENANT_ID, read_rows, row_to_event

_N_COLS = 43


def _synthetic_row(*, protocol="tcp", service="http", flag="SF", src_bytes=181, dst_bytes=5450, duration=0, label="normal") -> list[str]:
    row = ["0"] * _N_COLS
    row[0] = str(duration)
    row[1] = protocol
    row[2] = service
    row[3] = flag
    row[4] = str(src_bytes)
    row[5] = str(dst_bytes)
    row[41] = label
    row[42] = "20"
    return row


def test_read_rows_parses_valid_file(tmp_path):
    path = tmp_path / "kdd.txt"
    path.write_text("\n".join(",".join(_synthetic_row()) for _ in range(5)) + "\n")
    rows = read_rows(path, limit=None)
    assert len(rows) == 5
    assert all(len(r) == _N_COLS for r in rows)


def test_read_rows_respects_limit(tmp_path):
    path = tmp_path / "kdd.txt"
    path.write_text("\n".join(",".join(_synthetic_row()) for _ in range(10)) + "\n")
    rows = read_rows(path, limit=3)
    assert len(rows) == 3


def test_read_rows_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_rows(tmp_path / "does-not-exist.txt", limit=None)


def test_read_rows_wrong_column_count_raises(tmp_path):
    path = tmp_path / "kdd.txt"
    path.write_text("only,three,columns\n")
    with pytest.raises(ValueError, match="expected 43 columns"):
        read_rows(path, limit=None)


def test_row_to_event_maps_real_fields():
    row = _synthetic_row(protocol="tcp", service="http", flag="SF", src_bytes=181, dst_bytes=5450, duration=0)
    occurred_at = datetime(2026, 1, 1, tzinfo=UTC)
    event = row_to_event(row, index=5, occurred_at=occurred_at, tenant_id=DEFAULT_DEMO_TENANT_ID)

    assert event["tenant_id"] == str(DEFAULT_DEMO_TENANT_ID)
    assert event["protocol"] == "tcp"
    assert event["app_protocol"] == "http"
    assert event["verdict"] == "SF"
    assert event["bytes_sent"] == 181
    assert event["bytes_received"] == 5450
    assert event["src_ip"] == "10.50.0.5"
    assert event["dst_ip"] == "10.60.0.6"  # _DEST_POOL[5 % 16] = pool[5] = "10.60.0.6"
    assert event["occurred_at"] == occurred_at.isoformat()
    assert "ended_at" not in event  # duration was 0


def test_row_to_event_sets_ended_at_when_duration_positive():
    row = _synthetic_row(duration=30)
    occurred_at = datetime(2026, 1, 1, tzinfo=UTC)
    event = row_to_event(row, index=0, occurred_at=occurred_at, tenant_id=DEFAULT_DEMO_TENANT_ID)
    assert event["ended_at"] == "2026-01-01T00:00:30+00:00"


def test_row_to_event_placeholder_ips_are_deterministic():
    row = _synthetic_row()
    occurred_at = datetime(2026, 1, 1, tzinfo=UTC)
    e1 = row_to_event(row, index=42, occurred_at=occurred_at, tenant_id=DEFAULT_DEMO_TENANT_ID)
    e2 = row_to_event(row, index=42, occurred_at=occurred_at, tenant_id=DEFAULT_DEMO_TENANT_ID)
    assert e1["src_ip"] == e2["src_ip"]
    assert e1["dst_ip"] == e2["dst_ip"]


REDIS_URL = "redis://localhost:6379"


async def _redis_available() -> bool:
    try:
        client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1)
        await client.ping()
        await client.aclose()
        return True
    except Exception:
        return False


async def test_replay_publishes_real_rows_end_to_end(tmp_path):
    if not await _redis_available():
        pytest.skip("Redis not reachable at redis://localhost:6379")

    from replay.main import replay

    path = tmp_path / "kdd.txt"
    rows = [
        _synthetic_row(protocol="tcp", service="http", flag="SF", label="normal"),
        _synthetic_row(protocol="tcp", service="private", flag="REJ", label="neptune"),
        _synthetic_row(protocol="icmp", service="eco_i", flag="SF", label="normal"),
    ]
    path.write_text("\n".join(",".join(r) for r in rows) + "\n")

    stream = f"sml_test_replay_{uuid.uuid4().hex}"
    client = redis.Redis.from_url(REDIS_URL)
    try:
        result = await replay(file_path=path, redis_url=REDIS_URL, stream=stream, limit=None, interval_s=0.0)
        assert result == {"sent": 3, "total_rows": 3}

        consumer = EventBusConsumer(redis_url=REDIS_URL, streams=[stream], group_id="test-g", consumer_name="c1")
        await consumer.start()
        received = []

        async def handler(record):
            received.append(json.loads(record.value))

        handled = await consumer.run_once(handler, timeout_ms=500)
        await consumer.stop()

        assert handled == 3
        assert {e["app_protocol"] for e in received} == {"http", "private", "eco_i"}
        assert all(e["source_type"] == "network_flow" for e in received)
        assert all(e["tenant_id"] == str(DEFAULT_DEMO_TENANT_ID) for e in received)
    finally:
        await client.delete(stream)
        await client.aclose()
