"""HTTP surface for the memory layer: ingest a detection event, ask a
natural-language question, fetch one memory, trigger consolidation.

Two real gaps, stated plainly rather than hidden behind a working-looking
endpoint:

1. **Tenant scoping is not a security boundary yet.** `get_tenant_id()`
   below reads `X-Tenant-Id` directly off the request — there is no
   session/JWT verification behind it (SentinelMesh's `app/security/`
   isn't ported here — see README). Any caller can claim any tenant. This
   is fine for a single-analyst free-tier demo; it is not fine the moment
   more than one tenant's data lives in the same deployment. Replacing
   this with a verified-session dependency is a drop-in swap — every route
   below already takes `tenant_id` as an opaque dependency, not a client-
   trusted field it reads itself.

2. **Consolidation has no scheduler yet.** `/consolidate` runs dedup +
   pattern extraction synchronously, on request, rather than as the
   periodic background job `consolidation.py`'s own docstring describes.
   Wiring an APScheduler job belongs in `app/main.py` (not yet built) —
   this endpoint exists so a demo can trigger it without that job.

`app.state.memory_store` and `app.state.session_factory` are expected to
be set by `app/main.py`'s lifespan (not yet built). Until then, this
router is exercised directly in tests against a minimal FastAPI app that
sets both.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.memory.consolidation import deduplicate, extract_patterns
from app.memory.ingest import event_to_content, ingest_event
from app.memory.nl_query import answer_query, hybrid_retrieve
from app.memory.store import MemoryStore

__all__ = ["router"]

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


# -- dependencies -----------------------------------------------------------


def get_memory_store(request: Request) -> MemoryStore:
    return request.app.state.memory_store


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with session_scope(request.app.state.session_factory) as session:
        yield session


def get_tenant_id(x_tenant_id: uuid.UUID = Header(..., alias="X-Tenant-Id")) -> uuid.UUID:
    """Placeholder for a verified-session tenant lookup — see module
    docstring, gap 1. Trusts the header as-is."""
    return x_tenant_id


# -- request/response models -------------------------------------------------


class IngestRequest(BaseModel):
    title: str
    description: str
    severity: str
    technique_ids: list[str] = []
    entities: list[dict[str, str]] = []
    memory_type: str = "episodic"
    importance: float = 0.5
    emotional: float = 0.0
    tags: list[str] = []


class IngestResponse(BaseModel):
    accepted: bool
    memory_id: uuid.UUID | None
    poisoning: dict[str, Any] | None


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5


class MemoryOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    content: str
    memory_type: str
    importance_score: float
    emotional_score: float
    retrieval_count: int
    is_archived: bool
    is_poisoned: bool
    tags: list[str]
    created_at: datetime
    last_retrieved_at: datetime | None


class ConsolidateResponse(BaseModel):
    deduplicate: dict[str, Any]
    patterns: list[dict[str, Any]]


# -- routes -------------------------------------------------------------------


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    memory_type: str | None = None,
    limit: int = 100,
    include_archived: bool = False,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    store: MemoryStore = Depends(get_memory_store),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Chronological listing, not a search — backs the frontend's alert
    feed and relationship graph (graph edges are parsed client-side from
    `tags`' `linked_to:<id>` entries; there is no separate relationship
    endpoint, per the "linking is a tags entry, not a graph edge" note in
    `app/detection/consumer.py`)."""
    return await store.list_recent(
        session, tenant_id, memory_type=memory_type, limit=limit, include_archived=include_archived
    )


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    body: IngestRequest,
    response: Response,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    store: MemoryStore = Depends(get_memory_store),
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    content = event_to_content(
        title=body.title,
        description=body.description,
        severity=body.severity,
        technique_ids=body.technique_ids,
        entities=body.entities,
    )
    result = await ingest_event(
        store,
        session,
        tenant_id=tenant_id,
        content=content,
        memory_type=body.memory_type,
        importance=body.importance,
        emotional=body.emotional,
        tags=body.tags,
    )
    # 201 only when something was actually stored -- a rejected (injection)
    # or flagged (contradiction) outcome is a legitimate screening result,
    # not an error, but "rejected, nothing created" isn't a 201 either.
    response.status_code = 201 if result.accepted else 200
    return IngestResponse(
        accepted=result.accepted,
        memory_id=result.row.id if result.row else None,
        poisoning=result.poisoning,
    )


@router.post("/query")
async def query(
    body: QueryRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    store: MemoryStore = Depends(get_memory_store),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ranked = await hybrid_retrieve(store, session, body.query, tenant_id=tenant_id, top_k=body.top_k)
    return answer_query(body.query, ranked)


@router.get("/{memory_id}", response_model=MemoryOut)
async def get_memory(
    memory_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    store: MemoryStore = Depends(get_memory_store),
    session: AsyncSession = Depends(get_session),
) -> Any:
    row = await store.get(session, memory_id)
    # Wrong tenant is treated identically to "doesn't exist" -- never
    # confirm another tenant's memory id even exists.
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="memory not found")
    return row


@router.post("/consolidate", response_model=ConsolidateResponse)
async def consolidate(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    store: MemoryStore = Depends(get_memory_store),
    session: AsyncSession = Depends(get_session),
) -> ConsolidateResponse:
    dedup_result = await deduplicate(store, session, tenant_id)
    patterns = await extract_patterns(store, session, tenant_id)
    return ConsolidateResponse(
        deduplicate=dedup_result,
        patterns=[{**p, "member_ids": [str(i) for i in p["member_ids"]]} for p in patterns],
    )
