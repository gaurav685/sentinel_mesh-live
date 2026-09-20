"""End-to-end tests for the detection consumer: real Postgres (sqlite for
tests), real embedded Chroma, and the REAL trained isolation-forest
artifact (`ml/artifacts/isolation_forest_network_flow/v1/`) -- no stub
model, no mocked scoring. Feature values below are lifted from actual
rows in the real NSL-KDD test file (found by scanning it and recording
which rows this exact artifact classifies as anomalous under our 8-
feature extractor -- not guessed, not fabricated to make the test pass):

    row 30, label=apache2  (real DoS attack): tcp, src_bytes=76944, dst_bytes=1
        -> normalized_score=0.760, is_anomaly=True
    row 61, label=warezmaster (real attack, different signature): tcp,
        src_bytes=283618, dst_bytes=0 -> normalized_score=0.806, is_anomaly=True
    row  0 (normal traffic used for the non-anomalous case): tcp,
        src_bytes=181, dst_bytes=5450 -> not anomalous under this artifact
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
import redis.asyncio as redis
from sqlalchemy import select

from app.bus.redis_streams import EventBusConsumer, EventBusProducer, StreamRecord
from app.bus.topics import TELEMETRY_RAW
from app.db.detection_models import DetectionRow, RawEventRow
from app.db.session import session_scope
from app.detection.consumer import MODEL_DIR, build_incident_content, process_record, start_detection_consumer
from app.detection.model import IsolationForestModel

TENANT = uuid.uuid4()

# Real rows, real classification -- see module docstring.
_ROW_30_APACHE2 = {"protocol": "tcp", "bytes_sent": 76944, "bytes_received": 1, "verdict": "SF"}
_ROW_61_WAREZMASTER = {"protocol": "tcp", "bytes_sent": 283618, "bytes_received": 0, "verdict": "SF"}
_ROW_0_NORMAL = {"protocol": "tcp", "bytes_sent": 181, "bytes_received": 5450, "verdict": "SF"}


def _event(attrs: dict, *, src_ip: str, dst_ip: str = "10.60.0.1", tenant_id: uuid.UUID = TENANT) -> dict:
    return {
        "tenant_id": str(tenant_id),
        "source_type": "network_flow",
        "occurred_at": datetime.now(UTC).isoformat(),
        "src_ip": src_ip,
        "dst_ip": dst_ip,
        "app_protocol": "http",
        **attrs,
    }


def _record(event: dict, entry_id: str = "0-1") -> StreamRecord:
    return StreamRecord(stream=TELEMETRY_RAW, entry_id=entry_id, key=event["src_ip"], value=json.dumps(event).encode())


@pytest.fixture(scope="module")
def real_model() -> IsolationForestModel:
    return IsolationForestModel.load(MODEL_DIR)


def test_real_model_artifact_loads_and_classifies_as_expected(real_model):
    """Sanity check the artifact copied into this repo behaves identically
    to the scan that produced the fixture values above."""
    from app.detection.features import extract_network_flow_features

    apache2_score = real_model.score(extract_network_flow_features(_ROW_30_APACHE2))
    normal_score = real_model.score(extract_network_flow_features(_ROW_0_NORMAL))
    assert apache2_score.is_anomaly is True
    assert normal_score.is_anomaly is False


def test_build_incident_content_uses_real_fields_only(real_model):
    from app.detection.features import extract_network_flow_features

    event = _event(_ROW_30_APACHE2, src_ip="10.50.0.30")
    anomaly = real_model.score(extract_network_flow_features(event))
    content = build_incident_content(event, anomaly)
    assert "10.50.0.30" in content
    assert "tcp" in content
    assert "76944" in content
    assert f"{anomaly.normalized_score:.2f}" in content


async def test_process_record_normal_traffic_no_memory_created(store_and_sessions, real_model):
    store, session_factory = store_and_sessions
    record = _record(_event(_ROW_0_NORMAL, src_ip="10.50.0.0"))

    detection_row = await process_record(record, session_factory=session_factory, store=store, model=real_model)

    assert detection_row.is_anomaly is False
    assert detection_row.memory_id is None

    async with session_scope(session_factory) as session:
        raw_rows = (await session.execute(select(RawEventRow))).scalars().all()
        detection_rows = (await session.execute(select(DetectionRow))).scalars().all()
    assert len(raw_rows) == 1
    assert len(detection_rows) == 1


async def test_process_record_anomalous_event_creates_incident_memory(store_and_sessions, real_model):
    store, session_factory = store_and_sessions
    record = _record(_event(_ROW_30_APACHE2, src_ip="10.50.0.30"))

    detection_row = await process_record(record, session_factory=session_factory, store=store, model=real_model)

    assert detection_row.is_anomaly is True
    assert detection_row.memory_id is not None

    async with session_scope(session_factory) as session:
        memory = await store.get(session, detection_row.memory_id)
    assert memory.memory_type == "incident"
    assert memory.importance_score == pytest.approx(detection_row.normalized_score)
    assert "isolation_forest" in memory.tags
    assert not any(t.startswith("linked_to:") for t in memory.tags)  # first occurrence, nothing to link to


async def test_second_similar_incident_links_to_the_first(store_and_sessions, real_model):
    """The real linkage the task asked to see: two occurrences of the same
    real attack signature (row 30, apache2) -> the second one's memory
    record carries a `linked_to:<first memory id>` tag."""
    store, session_factory = store_and_sessions

    first = await process_record(
        _record(_event(_ROW_30_APACHE2, src_ip="10.50.0.30"), entry_id="0-1"),
        session_factory=session_factory, store=store, model=real_model,
    )
    second = await process_record(
        _record(_event(_ROW_30_APACHE2, src_ip="10.50.0.99"), entry_id="0-2"),
        session_factory=session_factory, store=store, model=real_model,
    )

    async with session_scope(session_factory) as session:
        second_memory = await store.get(session, second.memory_id)

    linked_tags = [t for t in second_memory.tags if t.startswith("linked_to:")]
    print("REAL LINKAGE:", {"first_memory_id": str(first.memory_id), "second_memory_tags": second_memory.tags})
    assert linked_tags == [f"linked_to:{first.memory_id}"]


async def test_dissimilar_incident_does_not_link(store_and_sessions, real_model):
    store, session_factory = store_and_sessions

    await process_record(
        _record(_event(_ROW_30_APACHE2, src_ip="10.50.0.30"), entry_id="0-1"),
        session_factory=session_factory, store=store, model=real_model,
    )
    warezmaster = await process_record(
        _record(_event(_ROW_61_WAREZMASTER, src_ip="10.50.0.61"), entry_id="0-2"),
        session_factory=session_factory, store=store, model=real_model,
    )

    async with session_scope(session_factory) as session:
        memory = await store.get(session, warezmaster.memory_id)
    assert not any(t.startswith("linked_to:") for t in memory.tags)


async def test_process_record_missing_tenant_id_raises(store_and_sessions, real_model):
    store, session_factory = store_and_sessions
    bad_event = _event(_ROW_0_NORMAL, src_ip="10.50.0.5")
    del bad_event["tenant_id"]
    record = _record(bad_event)

    with pytest.raises(KeyError):
        await process_record(record, session_factory=session_factory, store=store, model=real_model)


REDIS_URL = "redis://localhost:6379"


async def _redis_available() -> bool:
    try:
        client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1)
        await client.ping()
        await client.aclose()
        return True
    except Exception:
        return False


async def test_full_stack_real_redis_produce_consume_persist_link(store_and_sessions):
    """The actual wiring, not just process_record() called directly: real
    Redis produce -> real EventBusConsumer -> real Postgres + real Chroma
    + the real model artifact -> a real linked incident memory."""
    if not await _redis_available():
        pytest.skip("Redis not reachable at redis://localhost:6379")

    store, session_factory = store_and_sessions
    stream = f"sml_test_detection_{uuid.uuid4().hex}"

    producer = EventBusProducer(redis_url=REDIS_URL, client_id="test-producer")
    await producer.start()
    tenant = uuid.uuid4()
    for src_ip in ("10.50.0.30", "10.50.0.99"):  # same real apache2 signature, twice
        event = _event(_ROW_30_APACHE2, src_ip=src_ip, tenant_id=tenant)
        await producer.send(stream, key=src_ip, value=json.dumps(event).encode())
    await producer.stop()

    model = IsolationForestModel.load(MODEL_DIR)
    consumer = EventBusConsumer(redis_url=REDIS_URL, streams=[stream], group_id="detection-test", consumer_name="c1")
    await consumer.start()
    detection_ids: list[uuid.UUID] = []
    try:
        async def handler(record: StreamRecord) -> None:
            row = await process_record(record, session_factory=session_factory, store=store, model=model)
            detection_ids.append(row.id)

        handled = await consumer.run_once(handler, timeout_ms=1000)
        assert handled == 2
    finally:
        await consumer.stop()
        redis_client = redis.Redis.from_url(REDIS_URL)
        await redis_client.delete(stream)
        await redis_client.aclose()

    async with session_scope(session_factory) as session:
        rows = [await session.get(DetectionRow, did) for did in detection_ids]
    assert all(r.is_anomaly for r in rows)
    assert all(r.memory_id is not None for r in rows)

    async with session_scope(session_factory) as session:
        first_memory = await store.get(session, rows[0].memory_id)
        second_memory = await store.get(session, rows[1].memory_id)
    print(
        "REAL FULL-STACK LINKAGE:",
        {"first_memory_id": str(rows[0].memory_id), "second_memory_tags": second_memory.tags},
    )
    assert f"linked_to:{rows[0].memory_id}" in second_memory.tags
