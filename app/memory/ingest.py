"""Turns a confirmed detection/attack-chain event into a memory record.

The single place `contradiction.analyze_for_poisoning`'s result gets acted
on — `contradiction.py` deliberately returns an analysis only and mutates
nothing, per its own docstring; this module makes the actual policy call:

  - **injection-pattern content is REJECTED outright, never stored.**
    Storing a detected prompt-injection payload — even flagged
    `is_poisoned=True` — still leaves it sitting in corpus text that a
    future NL-query answer (`nl_query.py`) could echo back verbatim to an
    analyst. Not worth the risk for something that was never a real
    memory in the first place.
  - **a genuine contradiction is STORED, but flagged `is_poisoned=True`**,
    which excludes it from `vector_search`'s default (active-only)
    results. Not silently discarded: a contradicting security claim (e.g.
    "host-42 compromised" vs "host-42 cleared") is itself evidence an
    analyst may need to see later, not noise to throw away — the same
    non-fabrication principle SentinelMesh's reporting layer applies
    (never hide a real finding).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.memory_models import MemoryRecordRow
from app.memory.contradiction import analyze_for_poisoning
from app.memory.store import MemoryStore

__all__ = ["IngestResult", "event_to_content", "ingest_event"]


@dataclass
class IngestResult:
    accepted: bool
    row: MemoryRecordRow | None
    poisoning: dict[str, Any] | None


def event_to_content(
    *,
    title: str,
    description: str,
    severity: str,
    technique_ids: list[str] | None = None,
    entities: list[dict[str, str]] | None = None,
) -> str:
    """Renders a detection/attack-chain event's structured fields (the
    shape SentinelMesh's `/api/v1/soc/detections` already returns —
    `title`, `description`, `severity`, `technique_ids`, `entities`) into
    the free text `core.py`'s BM25/TF-IDF/contradiction algorithms operate
    on. Deterministic and lossy by design: this is what gets indexed and
    what an analyst reads back via `nl_query.py`, not a copy of the full
    event."""
    entity_str = ", ".join(f"{e['kind']}={e['value']}" for e in (entities or []))
    technique_str = ", ".join(technique_ids) if technique_ids else "none"
    return (
        f"{title}. {description} "
        f"severity={severity} techniques={technique_str} entities={entity_str}"
    ).strip()


async def ingest_event(
    store: MemoryStore,
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    content: str,
    memory_type: str = "episodic",
    importance: float = 0.5,
    emotional: float = 0.0,
    tags: list[str] | None = None,
) -> IngestResult:
    poisoning = await analyze_for_poisoning(store, tenant_id=tenant_id, content=content)

    if poisoning is not None and poisoning["type"] == "injection":
        return IngestResult(accepted=False, row=None, poisoning=poisoning)

    row = await store.add(
        session,
        tenant_id=tenant_id,
        content=content,
        memory_type=memory_type,
        importance=importance,
        emotional=emotional,
        tags=tags,
    )

    if poisoning is not None and poisoning["type"] == "contradiction":
        # Same session -> SQLAlchemy's identity map returns this exact `row`
        # object from set_status()'s internal fetch, so `row.is_poisoned` is
        # already True on return; no separate re-fetch needed here.
        await store.set_status(session, row.id, poisoned=True)

    return IngestResult(accepted=True, row=row, poisoning=poisoning)
