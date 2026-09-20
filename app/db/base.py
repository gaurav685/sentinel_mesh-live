"""Declarative base + timestamp mixin.

Simplification vs SentinelMesh (documented, not hidden): the original
`sm_common.ids.uuid7` generator isn't ported here — this project uses plain
`uuid4` for primary keys. uuid7's benefit (time-sortable IDs that keep
Postgres index inserts sequential) matters at SentinelMesh's production
write volume; at this project's demo scale it isn't worth pulling in an
extra dependency for.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


def new_id() -> uuid.UUID:
    return uuid.uuid4()


__all__ = ["Base", "TimestampMixin", "new_id"]
