"""HTTP surface for detection data: the live alert feed, dashboard stat
tiles, MITRE technique + threat score per detection, attack chains, and
chain narrative reports.

No relationship-graph endpoint here — graph edges come from
`app/memory/routes.py`'s memory list, since "linking" lives on the memory
record's `tags`, not on `DetectionRow` (`app/detection/consumer.py`'s
"Linking is a tags entry, not a graph edge" note).

Same tenant-header placeholder gap as `app/memory/routes.py` — reuses its
`get_tenant_id`/`get_session` dependencies rather than redefining them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.llm import LLMUnavailable
from app.chat.report import REPORT_DISCLAIMER, generate_chain_report
from app.db.detection_models import DetectionRow, RawEventRow
from app.db.memory_models import MemoryRecordRow
from app.detection.chains import Chain, build_chains
from app.detection.scoring import compute_threat_score
from app.memory.routes import get_session, get_tenant_id
from app.mitre.catalog import Technique, map_to_technique
from app.rate_limit import enforce_llm_rate_limit

__all__ = ["router"]

router = APIRouter(prefix="/api/v1", tags=["detections"])


class RawEventSummary(BaseModel):
    source_type: str
    src_ip: str | None = None
    dst_ip: str | None = None
    protocol: str | None = None
    app_protocol: str | None = None
    bytes_sent: int | None = None
    bytes_received: int | None = None
    verdict: str | None = None


class TechniqueOut(BaseModel):
    id: str
    name: str
    tactic: str
    reason: str


class DetectionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    detector: str
    model_version: str
    score: float
    normalized_score: float
    threshold: float
    is_anomaly: bool
    memory_id: uuid.UUID | None
    feature_values: dict[str, float]
    raw_event: RawEventSummary
    technique: TechniqueOut | None
    chain_length: int
    threat_score: float


class StatsOut(BaseModel):
    total_events: int
    anomaly_count: int
    memory_count: int


def _technique_out(technique: Technique | None, reason: str) -> TechniqueOut | None:
    if technique is None:
        return None
    return TechniqueOut(id=technique.id, name=technique.name, tactic=technique.tactic, reason=reason)


async def _chain_length_by_memory_id(session: AsyncSession, tenant_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """One `build_chains()` call, reused across every detection in a list
    response -- calling it per-detection would be an O(n) chain rebuild
    for each of n detections."""
    chains = await build_chains(session, tenant_id)
    lengths: dict[uuid.UUID, int] = {}
    for chain in chains:
        for memory in chain.memories:
            lengths[memory.id] = chain.length
    return lengths


@router.get("/detections", response_model=list[DetectionOut])
async def list_detections(
    limit: int = 50,
    memory_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> list[DetectionOut]:
    stmt = (
        select(DetectionRow, RawEventRow)
        .join(RawEventRow, DetectionRow.raw_event_id == RawEventRow.id)
        .where(DetectionRow.tenant_id == tenant_id)
    )
    if memory_id is not None:
        # Exact lookup for the incident detail page's "show the detection
        # that produced this memory" panel -- avoids the frontend fetching
        # every recent detection just to find one by memory_id client-side.
        stmt = stmt.where(DetectionRow.memory_id == memory_id)
    stmt = stmt.order_by(DetectionRow.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).all()

    chain_lengths = await _chain_length_by_memory_id(session, tenant_id)

    out: list[DetectionOut] = []
    for detection, raw_event in rows:
        payload = raw_event.payload
        mapping = map_to_technique(
            is_anomaly=detection.is_anomaly,
            protocol=payload.get("protocol"),
            app_protocol=payload.get("app_protocol"),
            verdict=payload.get("verdict"),
            bytes_sent=payload.get("bytes_sent"),
            bytes_received=payload.get("bytes_received"),
        )
        chain_length = chain_lengths.get(detection.memory_id, 1) if detection.memory_id else 1
        threat_score = compute_threat_score(
            confidence=detection.normalized_score, chain_length=chain_length, technique=mapping.technique
        )
        out.append(
            DetectionOut(
                id=detection.id,
                created_at=detection.created_at,
                detector=detection.detector,
                model_version=detection.model_version,
                score=detection.score,
                normalized_score=detection.normalized_score,
                threshold=detection.threshold,
                is_anomaly=detection.is_anomaly,
                memory_id=detection.memory_id,
                feature_values=detection.feature_values,
                raw_event=RawEventSummary(
                    source_type=raw_event.source_type,
                    src_ip=payload.get("src_ip"),
                    dst_ip=payload.get("dst_ip"),
                    protocol=payload.get("protocol"),
                    app_protocol=payload.get("app_protocol"),
                    bytes_sent=payload.get("bytes_sent"),
                    bytes_received=payload.get("bytes_received"),
                    verdict=payload.get("verdict"),
                ),
                technique=_technique_out(mapping.technique, mapping.reason),
                chain_length=chain_length,
                threat_score=threat_score,
            )
        )
    return out


@router.get("/stats", response_model=StatsOut)
async def stats(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> StatsOut:
    total_events = (
        await session.execute(
            select(func.count()).select_from(RawEventRow).where(RawEventRow.tenant_id == tenant_id)
        )
    ).scalar_one()
    anomaly_count = (
        await session.execute(
            select(func.count())
            .select_from(DetectionRow)
            .where(DetectionRow.tenant_id == tenant_id, DetectionRow.is_anomaly.is_(True))
        )
    ).scalar_one()
    memory_count = (
        await session.execute(
            select(func.count())
            .select_from(MemoryRecordRow)
            .where(MemoryRecordRow.tenant_id == tenant_id, MemoryRecordRow.is_archived.is_(False))
        )
    ).scalar_one()
    return StatsOut(total_events=total_events, anomaly_count=anomaly_count, memory_count=memory_count)


# -- attack chains ------------------------------------------------------------


class ChainMemoryOut(BaseModel):
    id: uuid.UUID
    content: str
    created_at: datetime
    importance_score: float
    is_archived: bool
    tags: list[str]


class ChainOut(BaseModel):
    chain_id: str
    length: int
    first_seen: datetime
    last_seen: datetime
    memories: list[ChainMemoryOut]


def _chain_out(chain: Chain) -> ChainOut:
    return ChainOut(
        chain_id=chain.chain_id,
        length=chain.length,
        first_seen=chain.memories[0].created_at,
        last_seen=chain.memories[-1].created_at,
        memories=[
            ChainMemoryOut(
                id=m.id,
                content=m.content,
                created_at=m.created_at,
                importance_score=m.importance_score,
                is_archived=m.is_archived,
                tags=m.tags,
            )
            for m in chain.memories
        ],
    )


@router.get("/chains", response_model=list[ChainOut])
async def list_chains(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> list[ChainOut]:
    chains = await build_chains(session, tenant_id)
    return [_chain_out(c) for c in chains]


@router.get("/chains/{chain_id}", response_model=ChainOut)
async def get_chain(
    chain_id: str,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> ChainOut:
    chains = await build_chains(session, tenant_id)
    chain = next((c for c in chains if c.chain_id == chain_id), None)
    if chain is None:
        raise HTTPException(status_code=404, detail="chain not found")
    return _chain_out(chain)


class ChainReportOut(BaseModel):
    chain_id: str
    report: str
    disclaimer: str
    generated_at: datetime


@router.post("/chains/{chain_id}/report", response_model=ChainReportOut, dependencies=[Depends(enforce_llm_rate_limit)])
async def report_chain(
    chain_id: str,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    session: AsyncSession = Depends(get_session),
) -> ChainReportOut:
    chains = await build_chains(session, tenant_id)
    chain = next((c for c in chains if c.chain_id == chain_id), None)
    if chain is None:
        raise HTTPException(status_code=404, detail="chain not found")

    try:
        report_text = await generate_chain_report(chain)
    except LLMUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ChainReportOut(
        chain_id=chain.chain_id, report=report_text, disclaimer=REPORT_DISCLAIMER, generated_at=datetime.now(UTC)
    )
