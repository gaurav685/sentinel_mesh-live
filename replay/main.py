"""The `replay` process (README: "Process topology: 2 deployables, not
19"). Streams real NSL-KDD rows into Redis Streams' `telemetry.raw`,
standing in for SentinelMesh's `scripts/ingest_nsl_kdd.py` (which posts to
a live `ingestion-gateway` over HTTP — a service this project doesn't
have). Kept as its own process, not folded into `api` as a background
task, specifically so it can be started/stopped independently for a demo
without touching the API process's state.

Same honesty as the script it's derived from, carried over unchanged:
  - NSL-KDD carries no real IP addresses (a property of the dataset, not
    stripped out here) — placeholder src/dst IPs are synthesized
    deterministically from row index, clearly placeholders, never
    presented as a captured address.
  - NSL-KDD carries no real capture timestamp — each row is stamped with
    the actual wall-clock time this process sends it, not a fabricated
    1999-era capture time.
  - The dataset's own label (row 41: normal/neptune/...) is printed
    locally as ground truth to compare against later; it is never sent to
    the pipeline as a hint.

Real, stated gap: nothing downstream of `telemetry.raw` consumes this yet
— `app/main.py`'s docstring already lists the ingestion/detection
background tasks as not built. This process populates the stream for
real today; nothing reads it back out until that consumer exists.

Every event is stamped with `tenant_id` (`--tenant-id`, default a fixed
placeholder demo tenant — see `DEFAULT_DEMO_TENANT_ID`) so
`app/detection/consumer.py` knows which tenant's Postgres rows and memory
records to write to. Real SentinelMesh derives this from the sending
sensor's credential; there is no sensor-auth layer here, so the replay
process is simply told which tenant it's replaying for.

Usage:
    python -m replay.main --file /c/Sentinel_Mesh/archive/KDDTest+.txt --limit 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.bus.redis_streams import EventBusProducer
from app.bus.topics import TELEMETRY_RAW
from app.config import settings

__all__ = ["DEFAULT_DEMO_TENANT_ID", "read_rows", "row_to_event", "replay", "main"]

DEFAULT_STREAM = TELEMETRY_RAW

#: A fixed, clearly-placeholder tenant id for demo runs with no real
#: tenant registry behind it (README: no tenant/auth subsystem ported).
DEFAULT_DEMO_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Row layout, 0-indexed (kddcup.names ordering, as used by NSL-KDD) —
# identical to scripts/ingest_nsl_kdd.py in the full SentinelMesh repo.
_N_COLS = 43  # 41 features + label + difficulty
_LABEL_COL = 41
_DURATION = 0
_PROTOCOL_TYPE = 1
_SERVICE = 2
_FLAG = 3
_SRC_BYTES = 4
_DST_BYTES = 5

_DEST_POOL = tuple(f"10.60.0.{n}" for n in range(1, 17))  # 16 fixed synthetic "servers"


def read_rows(path: Path, limit: int | None) -> list[list[str]]:
    if not path.exists():
        raise FileNotFoundError(f"NSL-KDD file not found: {path}")
    rows: list[list[str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) != _N_COLS:
            raise ValueError(f"expected {_N_COLS} columns, got {len(parts)}: {line[:80]!r}")
        rows.append(parts)
        if limit is not None and len(rows) >= limit:
            break
    if not rows:
        raise ValueError(f"no rows read from {path}")
    return rows


def row_to_event(row: list[str], index: int, occurred_at: datetime, tenant_id: uuid.UUID) -> dict[str, Any]:
    duration_s = float(row[_DURATION])
    src_bytes = int(row[_SRC_BYTES])
    dst_bytes = int(row[_DST_BYTES])
    event: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "source_type": "network_flow",
        "occurred_at": occurred_at.isoformat(),
        "src_ip": f"10.50.{(index // 256) % 256}.{index % 256}",
        "dst_ip": _DEST_POOL[index % len(_DEST_POOL)],
        "protocol": row[_PROTOCOL_TYPE],
        "app_protocol": row[_SERVICE],
        "bytes_sent": src_bytes,
        "bytes_received": dst_bytes,
        "verdict": row[_FLAG],
    }
    if duration_s > 0:
        event["ended_at"] = (occurred_at + timedelta(seconds=duration_s)).isoformat()
    return event


async def replay(
    *,
    file_path: Path,
    redis_url: str,
    tenant_id: uuid.UUID = DEFAULT_DEMO_TENANT_ID,
    stream: str = DEFAULT_STREAM,
    limit: int | None = 200,
    interval_s: float = 0.05,
) -> dict[str, int]:
    rows = read_rows(file_path, limit)
    print(f"read {len(rows)} real NSL-KDD rows from {file_path}")

    ground_truth = Counter(row[_LABEL_COL] for row in rows)
    print("dataset's own ground-truth labels (NOT sent to the pipeline):")
    for label, count in ground_truth.most_common():
        print(f"  {label}: {count}")

    now = datetime.now(UTC)
    producer = EventBusProducer(redis_url=redis_url, client_id="replay")
    await producer.start()
    sent = 0
    try:
        await producer.ping()
        for i, row in enumerate(rows):
            occurred_at = now + timedelta(seconds=i * interval_s)
            event = row_to_event(row, i, occurred_at, tenant_id)
            await producer.send(
                stream,
                key=event["src_ip"],
                value=json.dumps(event).encode("utf-8"),
                headers=[("source.type", "replay")],
            )
            sent += 1
            if sent % 100 == 0:
                print(f"  sent {sent}/{len(rows)} rows")
    finally:
        await producer.stop()

    print(f"done: {sent} rows sent to stream {stream!r}")
    return {"sent": sent, "total_rows": len(rows)}


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Replay real NSL-KDD rows into Redis Streams.")
    ap.add_argument("--file", required=True, type=Path, help="Path to a real NSL-KDD file (e.g. KDDTest+.txt)")
    ap.add_argument("--redis-url", default=settings.redis_url)
    ap.add_argument("--tenant-id", type=uuid.UUID, default=DEFAULT_DEMO_TENANT_ID)
    ap.add_argument("--stream", default=DEFAULT_STREAM)
    ap.add_argument("--limit", type=int, default=200, help="Rows to send (default 200)")
    ap.add_argument(
        "--interval-s", type=float, default=0.05, help="Simulated spacing between each row's occurred_at"
    )
    return ap.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    asyncio.run(
        replay(
            file_path=args.file,
            redis_url=args.redis_url,
            tenant_id=args.tenant_id,
            stream=args.stream,
            limit=args.limit,
            interval_s=args.interval_s,
        )
    )


if __name__ == "__main__":
    main()
