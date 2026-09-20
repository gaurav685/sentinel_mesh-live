"""Exercises `app/bus/redis_streams.py` against a real Redis (no mocking —
Streams' consumer-group/PEL semantics are exactly the part worth getting
wrong, and a mock would just encode my own assumptions back at me).
Requires Redis reachable at `redis://localhost:6379`; skips cleanly if not
(so the rest of the suite, which needs no external service, still runs
anywhere).
"""

from __future__ import annotations

import uuid

import pytest
import redis.asyncio as redis

from app.bus.redis_streams import EventBusConsumer, EventBusProducer, StreamRecord, dlq_payload

REDIS_URL = "redis://localhost:6379"


async def _redis_available() -> bool:
    try:
        client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1)
        await client.ping()
        await client.aclose()
        return True
    except Exception:
        return False


@pytest.fixture
async def stream_name():
    name = f"sml_test_{uuid.uuid4().hex}"
    if not await _redis_available():
        pytest.skip("Redis not reachable at redis://localhost:6379")
    yield name
    client = redis.Redis.from_url(REDIS_URL)
    await client.delete(name)
    await client.aclose()


def test_dlq_payload_shape():
    payload = dlq_payload(
        original=b'{"event": "x"}',
        error_type="ValueError",
        error_detail="bad data",
        consumer_group="g1",
        attempts=3,
    )
    import json

    body = json.loads(payload)
    assert body["original"] == '{"event": "x"}'
    assert body["error_type"] == "ValueError"
    assert body["attempts"] == 3
    assert body["consumer_group"] == "g1"


async def test_producer_send_and_ping(stream_name):
    producer = EventBusProducer(redis_url=REDIS_URL, client_id="test-producer")
    await producer.start()
    try:
        await producer.ping()
        await producer.send(stream_name, key="k1", value=b"hello world", headers=[("trace", "abc")])
    finally:
        await producer.stop()


async def test_producer_send_before_start_raises(stream_name):
    producer = EventBusProducer(redis_url=REDIS_URL, client_id="test-producer")
    with pytest.raises(RuntimeError):
        await producer.send(stream_name, key="k", value=b"x")


async def test_consumer_receives_produced_messages(stream_name):
    producer = EventBusProducer(redis_url=REDIS_URL, client_id="p1")
    await producer.start()
    await producer.send(stream_name, key="k1", value=b"first", headers=[("h", "1")])
    await producer.send(stream_name, key="k2", value=b"second")
    await producer.stop()

    received: list[StreamRecord] = []

    async def handler(record: StreamRecord) -> None:
        received.append(record)

    consumer = EventBusConsumer(
        redis_url=REDIS_URL, streams=[stream_name], group_id="g1", consumer_name="c1"
    )
    await consumer.start()
    try:
        handled = await consumer.run_once(handler, timeout_ms=500)
        assert handled == 2
        assert [r.value for r in received] == [b"first", b"second"]
        assert received[0].key == "k1"
        assert received[0].headers == [["h", "1"]]

        # Nothing left -- neither pending nor new.
        second_handled = await consumer.run_once(handler, timeout_ms=200)
        assert second_handled == 0
    finally:
        await consumer.stop()


async def test_consumer_redelivers_unacked_entry_after_handler_failure(stream_name):
    producer = EventBusProducer(redis_url=REDIS_URL, client_id="p1")
    await producer.start()
    await producer.send(stream_name, key="k1", value=b"will-fail-once")
    await producer.stop()

    attempts = 0

    async def flaky_handler(record: StreamRecord) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("simulated handler failure")

    consumer = EventBusConsumer(
        redis_url=REDIS_URL, streams=[stream_name], group_id="g1", consumer_name="c1"
    )
    await consumer.start()
    try:
        with pytest.raises(ValueError):
            await consumer.run_once(flaky_handler, timeout_ms=500)
        assert attempts == 1

        # Same entry redelivered from this consumer's own PEL, now succeeds.
        handled = await consumer.run_once(flaky_handler, timeout_ms=500)
        assert handled == 1
        assert attempts == 2

        # Fully acked now -- nothing left to redeliver.
        handled_again = await consumer.run_once(flaky_handler, timeout_ms=200)
        assert handled_again == 0
    finally:
        await consumer.stop()


async def test_two_consumers_same_group_do_not_duplicate_work(stream_name):
    producer = EventBusProducer(redis_url=REDIS_URL, client_id="p1")
    await producer.start()
    for i in range(4):
        await producer.send(stream_name, key=f"k{i}", value=str(i).encode())
    await producer.stop()

    consumer_a = EventBusConsumer(redis_url=REDIS_URL, streams=[stream_name], group_id="g1", consumer_name="a")
    consumer_b = EventBusConsumer(redis_url=REDIS_URL, streams=[stream_name], group_id="g1", consumer_name="b")
    await consumer_a.start()
    await consumer_b.start()

    seen: list[bytes] = []

    async def collect(record: StreamRecord) -> None:
        seen.append(record.value)

    try:
        handled_a = await consumer_a.run_once(collect, timeout_ms=500)
        handled_b = await consumer_b.run_once(collect, timeout_ms=200)
        assert handled_a + handled_b == 4
        assert len(seen) == 4
        assert len(set(seen)) == 4  # no duplicate delivery across the two consumers
    finally:
        await consumer_a.stop()
        await consumer_b.stop()
