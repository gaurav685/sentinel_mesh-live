"""Postgres schema for `core.py`'s `Memory` dataclass (Phase: SentinelMesh
Live memory layer). This is the durable side of the Chroma+Postgres bridge
in `app/memory/store.py` — Postgres holds every field; Chroma holds only
`(id, embedding, content, a few filter metadata fields)` and is treated as a
rebuildable cache (README: "Store substitutions").

Simplification vs SentinelMesh's `db/memory_models.py` (documented, not
hidden): `tenant_id` here is a plain indexed UUID column, not a foreign key
to a `tenant` table — this project doesn't port SentinelMesh's tenant/auth
subsystem. Enforcing tenant scoping is the caller's job (every store.py
method takes `tenant_id` explicitly and filters by it); there is no
database-level constraint stopping a bug from mixing tenants, the way
SentinelMesh's real FK would. Acceptable for a single/few-tenant demo, not
for a real multi-tenant deployment.

`tags` uses generic `JSON` (not Postgres `JSONB`) so the same model works
against both sqlite (tests/dev) and Postgres (production) — see
`app/config.py`'s `database_url`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from .base import Base, TimestampMixin, new_id

__all__ = ["MemoryRecordRow"]

_MEMORY_TYPES = ("episodic", "semantic", "preference", "goal", "failure")


class MemoryRecordRow(TimestampMixin, Base):
    __tablename__ = "memory_record"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(), nullable=False)

    content: Mapped[str] = mapped_column(Text(), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False, default="episodic")
    importance_score: Mapped[float] = mapped_column(Float(), nullable=False, default=0.5)
    emotional_score: Mapped[float] = mapped_column(Float(), nullable=False, default=0.0)
    retrieval_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    # Real bug, found running this against real Postgres (never surfaced by
    # any sqlite-backed test): a bare `Mapped[datetime]` infers a
    # timezone-naive column, but `store.mark_retrieved` writes
    # `datetime.now(timezone.utc)` (aware) into it -- asyncpg's strict
    # encoder raises `TypeError: can't subtract offset-naive and
    # offset-aware datetimes` on that mismatch (sqlite has no such
    # check). `DateTime(timezone=True)` matches every other timestamp
    # column in this project (see TimestampMixin).
    last_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    is_poisoned: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    tags: Mapped[list[str]] = mapped_column(JSON(), nullable=False, default=list)

    __table_args__ = (
        Index("ix_memory_record_tenant_active", "tenant_id", "is_archived", "is_poisoned"),
        Index("ix_memory_record_tenant_type", "tenant_id", "memory_type"),
        Index("ix_memory_record_tenant_last_seen", "tenant_id", "last_retrieved_at"),
    )
