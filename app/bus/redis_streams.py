"""Async Redis Streams producer/consumer — the free-tier replacement for
SentinelMesh's `sm_common.bus` Kafka wrapper. Same call shape deliberately
(`start`/`stop`/`send`/`run_once`/`run`/`ping`) — a service written against
this needs no code change if it's ever pointed back at Kafka, only the
class import changes.

Mapping from Kafka concepts to Redis Streams:
  - topic -> stream key
  - consumer group -> a Redis Streams consumer group (`XGROUP CREATE`)
  - partition/offset -> a stream entry ID (`<ms>-<seq>`), assigned by Redis
  - "manual commit after handling" -> `XACK`
  - "batch not committed on failure -> redelivered" -> an unacked entry
    stays in this consumer's Pending Entries List (PEL). `run_once` reads
    its own PEL first (`XREADGROUP ... 0`) before reading new entries
    (`XREADGROUP ... >`), so a crash mid-batch reprocesses exactly those
    entries on the next call, from this same consumer name.

Deliberate divergence from the Kafka wrapper's exact semantics: that
wrapper rewinds and redelivers the *entire* batch on any handler failure.
Here, each successfully-handled entry is ack'd individually as it
completes (`XACK` is built for exactly this); only the entry that raised
(and anything after it, never attempted) stays pending. This is strictly
finer-grained under the same requirement the Kafka wrapper already states
— handlers must be idempotent on event id — so it's not a weaker
guarantee, just a more precise one that happens to reprocess less.

Known limitation, not built here: no `XCLAIM`/`XAUTOCLAIM` reaper for a
truly *dead* consumer's abandoned pending entries — those stay assigned to
that consumer name until it restarts and reclaims them itself (`run_once`
checking its own PEL). Fine for this project's one-consumer-per-group-per-
process shape; a real multi-replica deployment would need a reaper, which
Kafka's automatic partition rebalance on member loss didn't need.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import redis.asyncio as redis

_log = logging.getLogger("sentinelmesh_live.bus.redis_streams")

__all__ = ["EventBusProducer", "EventBusConsumer", "StreamRecord", "dlq_payload"]

Headers = list[tuple[str, str]]

_MAX_DETAIL = 2000


def dlq_payload(
    *,
    original: bytes,
    error_type: str,
    error_detail: str,
    consumer_group: str,
    attempts: int,
    failed_at: datetime | None = None,
) -> bytes:
    """The canonical DLQ record shape (ported unchanged from the Kafka
    wrapper — pure serialization, no infra dependency): the original
    message plus why it failed."""
    return json.dumps(
        {
            "original": original.decode("utf-8", "replace"),
            "error_type": error_type,
            "error_detail": error_detail[:_MAX_DETAIL],
            "consumer_group": consumer_group,
            "attempts": attempts,
            "failed_at": (failed_at or datetime.now(timezone.utc)).isoformat(),
        }
    ).encode("utf-8")


class EventBusProducer:
    def __init__(self, *, redis_url: str, client_id: str) -> None:
        self._redis_url = redis_url
        self._client_id = client_id
        self._redis: redis.Redis | None = None

    async def start(self) -> None:
        if self._redis is None:
            self._redis = redis.Redis.from_url(self._redis_url, decode_responses=False)

    async def stop(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def send(self, stream: str, *, key: str, value: bytes, headers: Headers | None = None) -> None:
        if self._redis is None:
            raise RuntimeError("event bus producer is not started")
        fields = {
            "key": key.encode("utf-8"),
            "value": value,
            "headers": json.dumps(headers or []).encode("utf-8"),
        }
        await self._redis.xadd(stream, fields)

    async def ping(self) -> None:
        if self._redis is None:
            raise RuntimeError("event bus producer is not started")
        await self._redis.ping()

    async def __aenter__(self) -> EventBusProducer:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()


@dataclass
class StreamRecord:
    stream: str
    entry_id: str
    key: str
    value: bytes
    headers: Headers = field(default_factory=list)


RecordHandler = Callable[[StreamRecord], Awaitable[None]]


class EventBusConsumer:
    def __init__(
        self,
        *,
        redis_url: str,
        streams: list[str],
        group_id: str,
        consumer_name: str,
        max_records_per_poll: int = 200,
    ) -> None:
        self._redis_url = redis_url
        self._streams = streams
        self.group_id = group_id
        self._consumer_name = consumer_name
        self._max_records = max_records_per_poll
        self._redis: redis.Redis | None = None
        self._stopping = False
        self._batch_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._redis is not None:
            return
        self._redis = redis.Redis.from_url(self._redis_url, decode_responses=False)
        for stream in self._streams:
            try:
                await self._redis.xgroup_create(stream, self.group_id, id="0", mkstream=True)
            except redis.ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
        _log.info(
            "consumer_group_joined",
            extra={"group": self.group_id, "consumer": self._consumer_name, "streams": self._streams},
        )

    def request_stop(self) -> None:
        self._stopping = True

    async def stop(self) -> None:
        self._stopping = True
        if self._redis is not None:
            async with self._batch_lock:
                await self._redis.aclose()
                self._redis = None

    async def ping(self) -> None:
        if self._redis is None:
            raise RuntimeError("event bus consumer is not started")
        await self._redis.ping()

    @staticmethod
    def _decode(value: bytes | str) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else value

    def _to_record(self, stream: str, entry_id: bytes, fields: dict[bytes, bytes]) -> StreamRecord:
        key = self._decode(fields.get(b"key", b""))
        value = fields.get(b"value", b"")
        headers = json.loads(self._decode(fields.get(b"headers", b"[]")))
        return StreamRecord(stream=stream, entry_id=self._decode(entry_id), key=key, value=value, headers=headers)

    @staticmethod
    def _has_entries(batches: list) -> bool:
        return any(entries for _, entries in batches)

    async def run_once(self, handler: RecordHandler, *, timeout_ms: int = 1000) -> int:
        """Reclaims this consumer's own still-pending entries first (a
        crash between handling and ACKing leaves them there — see module
        docstring), then reads new entries if nothing was pending. ACKs
        each entry individually as its handler succeeds; a handler
        exception propagates after ACKing everything that succeeded before
        it, leaving the failing entry (and anything after it, unattempted)
        in the PEL for the next call."""
        if self._redis is None:
            raise RuntimeError("event bus consumer is not started")

        async with self._batch_lock:
            pending = await self._redis.xreadgroup(
                self.group_id, self._consumer_name, {s: "0" for s in self._streams}, count=self._max_records
            )
            if self._has_entries(pending):
                batches = pending
            else:
                batches = await self._redis.xreadgroup(
                    self.group_id,
                    self._consumer_name,
                    {s: ">" for s in self._streams},
                    count=self._max_records,
                    block=timeout_ms,
                )
                if not batches:
                    return 0

            handled = 0
            for stream_name, entries in batches:
                stream_str = self._decode(stream_name)
                for entry_id, fields in entries:
                    record = self._to_record(stream_str, entry_id, fields)
                    await handler(record)
                    await self._redis.xack(stream_str, self.group_id, record.entry_id)
                    handled += 1
            return handled

    async def run(self, handler: RecordHandler) -> None:
        while not self._stopping:
            try:
                await self.run_once(handler)
            except Exception:
                # Bug fixed here: this previously swallowed every failure
                # silently (no log at all), which would make a real handler
                # bug — a bad event, a model load failure — invisible; the
                # loop would just retry forever with no trace of why.
                _log.exception("consumer_run_once_failed", extra={"group": self.group_id})
                await asyncio.sleep(1.0)
