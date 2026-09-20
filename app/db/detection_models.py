"""Postgres schema for the detection pipeline (`app/detection/consumer.py`):
every classified telemetry event (`raw_event`) and every anomaly finding
above the model's threshold (`detection`).

Same simplification as `memory_models.py`, stated there and true here too:
`tenant_id` is a plain indexed UUID, not a foreign key to a `tenant` table
— this project doesn't port SentinelMesh's tenant/auth subsystem. Tenant
scoping is enforced by every query taking `tenant_id` explicitly, not by a
database constraint.
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from .base import Base, TimestampMixin, new_id

__all__ = ["RawEventRow", "DetectionRow"]


class RawEventRow(TimestampMixin, Base):
    __tablename__ = "raw_event"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # The event exactly as received off the stream (already-parsed JSON,
    # not the raw bytes) -- kept in full so a detection row's feature
    # values can always be traced back to what actually produced them.
    payload: Mapped[dict] = mapped_column(JSON(), nullable=False)

    __table_args__ = (Index("ix_raw_event_tenant_created", "tenant_id", "created_at"),)


class DetectionRow(TimestampMixin, Base):
    __tablename__ = "detection"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(), nullable=False)
    raw_event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("raw_event.id", ondelete="RESTRICT"), nullable=False
    )
    detector: Mapped[str] = mapped_column(String(32), nullable=False, default="isolation_forest")
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float(), nullable=False)
    normalized_score: Mapped[float] = mapped_column(Float(), nullable=False)
    threshold: Mapped[float] = mapped_column(Float(), nullable=False)
    is_anomaly: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    feature_values: Mapped[dict] = mapped_column(JSON(), nullable=False)
    # Set only when this detection resulted in a memory record being
    # created (see consumer.py's confidence-threshold gate) -- lets a
    # detection row and its incident memory be cross-referenced without a
    # dedicated relationship table.
    memory_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)

    __table_args__ = (
        Index("ix_detection_tenant_created", "tenant_id", "created_at"),
        Index("ix_detection_tenant_anomaly", "tenant_id", "is_anomaly"),
    )
