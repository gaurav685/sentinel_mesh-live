"""The detection consumer — the piece that makes this a threat-detection
system rather than plumbing. Consumes `telemetry.raw` (via
`app/bus/redis_streams.py`'s `EventBusConsumer`, already built and
tested), scores every event with the real trained isolation-forest
artifact, persists every classified record to Postgres, and — for
confidently anomalous ones — creates an incident memory in the already-
built memory layer, linking it to a similar past incident when one exists
rather than reusing the retrieval logic that already does that search.

What's real here vs. placeholder, stated the same way `replay/main.py`
states its own:
  - The isolation-forest scoring is the real, trained SentinelMesh
    artifact (`ml/artifacts/isolation_forest_network_flow/v1/`), not a
    stub. Its own documented limitation still applies: NSL-KDD-replayed
    events carry no real `dst_port`/packet-count/`direction`, so 5 of the
    8 model features are constant zero for every event this consumer will
    ever see from `replay/main.py` — only `proto_tcp` and the two byte-
    count features carry real signal for this dataset (see
    `app/detection/features.py`'s docstring).
  - "Linking" a new incident to a past one is a `tags` entry
    (`linked_to:<memory_id>`) on the new memory record, not a graph edge.
    No relationship/graph store was ever built for this project (the
    Neo4j AuraDB piece from the original scope never got past the
    planning stage) — `tags` is the real, queryable mechanism that
    actually exists, not a stand-in pretending to be a graph.
  - **Real bug found and fixed while testing this against real data**:
    the first version of the link decision used `store.vector_search`'s
    text similarity alone (over `build_incident_content`'s rendered
    description). Empirically, two *different* real attacks (NSL-KDD rows
    30/apache2 and 61/warezmaster) scored 0.68-0.75 text similarity —
    above the 0.5 threshold that would have linked them — because the
    description template's boilerplate ("Anomalous network_flow activity
    from ... isolation_forest normalized_score=... threshold=...")
    dominates TF-IDF regardless of the actual attack. Feature-vector
    cosine similarity doesn't fix this either: it's ~0.998 for *both* the
    same-signature pair and the different-attack pair, because NSL-KDD's
    own known limitation (5 of 8 features constant zero — see
    `features.py`) means nearly every event points in almost the same
    direction in this 8-D space regardless of magnitude; cosine similarity
    is scale-invariant, so it can't tell `bytes_sent=76944` from
    `bytes_sent=283618` apart. **Euclidean distance on the real feature
    vector does separate them** (0.69 for the true repeat vs. 1.48 for the
    different attack, vs. 9.96 for unrelated normal traffic — verified
    against real NSL-KDD rows, see `tests/unit/test_detection_consumer.py`),
    so that's what the actual link decision uses below.
    `store.vector_search` is still used, exactly as asked, as the
    candidate-retrieval step; the fix is in how a candidate gets confirmed
    as "the same signature," not in abandoning the reuse. Stated
    limitation, not silently ignored: `dst_port` is unscaled (0-65535)
    while the log-transformed features are bounded 0-30; if a future data
    source ever populates real ports, that dimension would dominate this
    distance calculation. Never happens today — NSL-KDD never populates
    `dst_port` — so not worth normalizing pre-emptively for a case that
    can't currently occur.
  - `tenant_id` comes from the event payload itself (`replay/main.py`
    stamps it) — there is still no sensor-auth layer deriving it from a
    verified credential, the same gap `routes.py`'s docstring already
    states for its own `X-Tenant-Id` header.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
import uuid
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bus.redis_streams import EventBusConsumer, StreamRecord
from app.bus.topics import TELEMETRY_RAW
from app.db.detection_models import DetectionRow, RawEventRow
from app.db.session import session_scope
from app.detection.features import FEATURE_NAMES, extract_network_flow_features
from app.detection.model import AnomalyScore, IsolationForestModel
from app.memory.ingest import ingest_event
from app.memory.store import MemoryStore

__all__ = [
    "MODEL_DIR",
    "MEMORY_CONFIDENCE_THRESHOLD",
    "MAX_LINK_FEATURE_DISTANCE",
    "TEXT_CANDIDATE_FLOOR",
    "build_incident_content",
    "process_record",
    "start_detection_consumer",
]

_log = logging.getLogger("sentinelmesh_live.detection.consumer")

#: Ported artifact location — same layout SentinelMesh's own `ml-inference`
#: expects (`model.joblib` + `metadata.json` under a versioned directory).
MODEL_DIR = Path(__file__).resolve().parents[2] / "ml" / "artifacts" / "isolation_forest_network_flow" / "v1"

#: Gate for creating an incident memory, separate from the model's own
#: `is_anomaly` decision (`normalized_score >= this artifact's own
#: threshold`, which happens to normalize to ~0.60 for this specific
#: artifact). This is a distinct, operator-tunable knob: an event can be a
#: real anomaly by the model's own decision boundary without being
#: confident enough to justify writing a full incident record. Chosen
#: empirically against this artifact's normalized-score distribution, not
#: derived from anything guaranteed to generalize to a different artifact.
MEMORY_CONFIDENCE_THRESHOLD = 0.6

#: Loose pre-filter on `store.vector_search`'s text similarity, just to
#: avoid pulling in completely unrelated candidates before the real
#: (feature-vector-distance) check below. Deliberately loose -- see the
#: module docstring's "real bug found" note for why text similarity alone
#: is not trustworthy enough to make the actual link decision.
TEXT_CANDIDATE_FLOOR = 0.2

#: Max Euclidean distance between two events' real 8-D feature vectors to
#: treat them as "the same signature." Empirically chosen against this
#: artifact's real distribution (see the module docstring): 0.69 for a
#: true repeat, 1.48 for a genuinely different real attack, 9.96 for
#: unrelated normal traffic. A heuristic threshold, not a calibrated one —
#: same status as `contradiction.py`'s poisoning threshold, and equally
#: liable to need retuning against a different artifact or dataset.
MAX_LINK_FEATURE_DISTANCE = 1.0


def build_incident_content(event: dict, anomaly: AnomalyScore) -> str:
    """Natural-language incident description, built only from real event
    fields and real model output -- no fabricated detail."""
    return (
        f"Anomalous network_flow activity from {event.get('src_ip', 'unknown-src')} "
        f"to {event.get('dst_ip', 'unknown-dst')} ({event.get('protocol', 'unknown')}/"
        f"{event.get('app_protocol', 'unknown')}). isolation_forest "
        f"normalized_score={anomaly.normalized_score:.2f} (threshold={anomaly.threshold:.2f}, "
        f"raw_score={anomaly.score:.4f}, model_version={anomaly.model_version}). "
        f"bytes_sent={event.get('bytes_sent', 0)} bytes_received={event.get('bytes_received', 0)} "
        f"verdict={event.get('verdict', 'unknown')}."
    )


async def process_record(
    record: StreamRecord,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    store: MemoryStore,
    model: IsolationForestModel,
    memory_confidence_threshold: float = MEMORY_CONFIDENCE_THRESHOLD,
) -> DetectionRow:
    """One `telemetry.raw` record -> one persisted `raw_event` row, one
    persisted `detection` row, and (only for a confidently anomalous
    event) one incident memory, possibly linked to a past one.

    Raises on a malformed event (bad JSON, missing `tenant_id`) rather
    than silently skipping it -- the caller (`EventBusConsumer.run_once`,
    already tested) leaves an entry that raised unacked, so a genuinely
    bad event is retried and visible in logs, not dropped without a
    trace."""
    start = time.monotonic()
    event = json.loads(record.value.decode("utf-8"))
    tenant_id = uuid.UUID(event["tenant_id"])

    features = extract_network_flow_features(event)
    anomaly = model.score(features)

    async with session_scope(session_factory) as session:
        raw_row = RawEventRow(
            tenant_id=tenant_id, source_type=event.get("source_type", "unknown"), payload=event
        )
        session.add(raw_row)
        await session.flush()

        detection_row = DetectionRow(
            tenant_id=tenant_id,
            raw_event_id=raw_row.id,
            detector="isolation_forest",
            model_version=anomaly.model_version,
            score=anomaly.score,
            normalized_score=anomaly.normalized_score,
            threshold=anomaly.threshold,
            is_anomaly=anomaly.is_anomaly,
            feature_values=dict(zip(FEATURE_NAMES, features)),
        )
        session.add(detection_row)
        await session.flush()

        if anomaly.is_anomaly and anomaly.normalized_score >= memory_confidence_threshold:
            content = build_incident_content(event, anomaly)

            # Reuses the existing search for candidate retrieval — does
            # not reimplement it. A vector_search restricted to
            # memory_type="incident" is the same lookup nl_query.py's
            # hybrid_retrieve is built on; full hybrid retrieval (BM25 +
            # decay ranking + reinforcement) is deliberately not used here
            # -- reinforcement is meant for an analyst's own NL query, not
            # a side effect of every incoming detection silently bumping
            # an unrelated memory's retrieval_count.
            candidates = await store.vector_search(
                content, tenant_id=tenant_id, top_k=5, memory_type="incident"
            )
            candidates = [c for c in candidates if c[1] >= TEXT_CANDIDATE_FLOOR]

            linked_memory_id: uuid.UUID | None = None
            if candidates:
                candidate_ids = [cid for cid, _sim, _content in candidates]
                past_detections = (
                    await session.execute(
                        select(DetectionRow).where(DetectionRow.memory_id.in_(candidate_ids))
                    )
                ).scalars().all()
                by_memory_id = {d.memory_id: d for d in past_detections}

                best_distance: float | None = None
                for candidate_id, _sim, _content in candidates:
                    past = by_memory_id.get(candidate_id)
                    if past is None:
                        continue
                    past_vector = np.array([past.feature_values[name] for name in FEATURE_NAMES])
                    distance = float(np.linalg.norm(np.array(features) - past_vector))
                    if best_distance is None or distance < best_distance:
                        best_distance = distance
                        linked_memory_id = candidate_id

                if best_distance is None or best_distance > MAX_LINK_FEATURE_DISTANCE:
                    linked_memory_id = None
                else:
                    _log.info(
                        "incident_linked",
                        extra={"linked_to": str(linked_memory_id), "feature_distance": best_distance},
                    )

            tags = ["isolation_forest"]
            if linked_memory_id is not None:
                tags.append(f"linked_to:{linked_memory_id}")

            result = await ingest_event(
                store,
                session,
                tenant_id=tenant_id,
                content=content,
                memory_type="incident",
                importance=anomaly.normalized_score,
                tags=tags,
            )
            if result.accepted and result.row is not None:
                detection_row.memory_id = result.row.id

        await session.commit()

    _log.debug(
        "detection_processed",
        extra={
            "is_anomaly": anomaly.is_anomaly,
            "normalized_score": anomaly.normalized_score,
            "latency_ms": round((time.monotonic() - start) * 1000, 1),
        },
    )
    return detection_row


async def start_detection_consumer(
    *,
    redis_url: str,
    session_factory: async_sessionmaker[AsyncSession],
    store: MemoryStore,
    model_dir: Path = MODEL_DIR,
    consumer_name: str | None = None,
    group_id: str = "detection-engine",
    memory_confidence_threshold: float = MEMORY_CONFIDENCE_THRESHOLD,
) -> tuple[EventBusConsumer, asyncio.Task[None], IsolationForestModel]:
    """Loads the real trained artifact, starts the consumer, and launches
    its `run()` loop as a background task. Returns the consumer/task/model
    so the caller (`app/main.py`) can shut down in the order the bus
    module's own docstring specifies (`request_stop()`, await the task,
    then `stop()`) and can expose the loaded model's version/checksum on a
    readiness check without reloading the artifact a second time.

    Real bug, found live: a fixed literal `consumer_name` default meant two
    physical processes (e.g. an orphaned leftover `api` process and a
    freshly started one, both pointed at the same Redis) silently
    registered under the *identical* Redis Streams consumer identity.
    Redis has no way to tell them apart, so each newly-arrived stream entry
    was delivered to whichever process's `XREADGROUP` call happened to win
    that race -- with zero error, on either side. Confirmed by directly
    reproducing it: sending events one at a time landed roughly half on
    each process's own database. `consumer_name` now defaults to a value
    that's actually unique per process (hostname + pid), so two instances
    -- accidental or, if this is ever scaled to multiple replicas,
    intentional -- get distinct identities instead of silently splitting
    the same one.
    """
    if consumer_name is None:
        consumer_name = f"detection-{socket.gethostname()}-{os.getpid()}"
    model = IsolationForestModel.load(model_dir)
    _log.info(
        "model_loaded",
        extra={"model_version": model.model_version, "artifact_checksum": model.artifact_checksum},
    )
    consumer = EventBusConsumer(
        redis_url=redis_url, streams=[TELEMETRY_RAW], group_id=group_id, consumer_name=consumer_name
    )
    await consumer.start()

    async def handler(record: StreamRecord) -> None:
        await process_record(
            record,
            session_factory=session_factory,
            store=store,
            model=model,
            memory_confidence_threshold=memory_confidence_threshold,
        )

    task = asyncio.create_task(consumer.run(handler))
    return consumer, task, model
