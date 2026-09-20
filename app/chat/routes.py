"""HTTP surface for the natural-language incident chat
(`app/chat/query.py`). Reuses `app/memory/routes.py`'s dependencies
(`get_memory_store`, `get_session`, `get_tenant_id`) rather than
redefining them -- same `app.state`, same placeholder-tenant-header gap
stated there (gap 1), not re-derived here.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.query import LLMUnavailable, answer_chat
from app.memory.routes import get_memory_store, get_session, get_tenant_id
from app.memory.store import MemoryStore
from app.rate_limit import enforce_llm_rate_limit

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    query: str
    top_k: int = 5


@router.post("/chat", dependencies=[Depends(enforce_llm_rate_limit)])
async def chat(
    body: ChatRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    store: MemoryStore = Depends(get_memory_store),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        return await answer_chat(store, session, body.query, tenant_id=tenant_id, top_k=body.top_k)
    except LLMUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
