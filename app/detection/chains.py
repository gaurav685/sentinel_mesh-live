"""Groups incident memories connected via the existing `linked_to` tag
(app/detection/consumer.py's real linking mechanism) into attack chains.

Reuses the linking data as-is -- does not reinvent it. `linked_to` edges
are currently produced one-hop (a new incident links to the single most
similar past one, `consumer.py`'s `MAX_LINK_FEATURE_DISTANCE` gate), but
this groups by connected component, not just pairs, so a longer real
chain (A links to B, a later C links to B too) is grouped as one chain of
three, not missed.

Includes archived incidents on purpose (same bug/fix as the frontend's
dashboard fetch): consolidation can archive one side of a real link, and
excluding it would silently drop the very evidence a chain exists to
show.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.memory_models import MemoryRecordRow

__all__ = ["Chain", "parse_linked_ids", "build_chains", "get_chain_for_memory"]


def parse_linked_ids(tags: list[str]) -> list[uuid.UUID]:
    ids = []
    for tag in tags:
        if tag.startswith("linked_to:"):
            try:
                ids.append(uuid.UUID(tag[len("linked_to:") :]))
            except ValueError:
                continue
    return ids


@dataclass(frozen=True)
class Chain:
    chain_id: str
    memories: list[MemoryRecordRow]  # chronological order, oldest first

    @property
    def length(self) -> int:
        return len(self.memories)


async def build_chains(
    session: AsyncSession, tenant_id: uuid.UUID, *, memory_type: str = "incident"
) -> list[Chain]:
    """Connected components over the `linked_to` graph, size >= 2 only --
    a single, un-linked incident isn't a chain, it's just an incident."""
    rows = (
        await session.execute(
            select(MemoryRecordRow).where(
                MemoryRecordRow.tenant_id == tenant_id, MemoryRecordRow.memory_type == memory_type
            )
        )
    ).scalars().all()
    by_id = {row.id: row for row in rows}

    adjacency: dict[uuid.UUID, set[uuid.UUID]] = {row.id: set() for row in rows}
    for row in rows:
        for target_id in parse_linked_ids(row.tags):
            if target_id in by_id:
                adjacency[row.id].add(target_id)
                adjacency[target_id].add(row.id)

    seen: set[uuid.UUID] = set()
    chains: list[Chain] = []
    for row in rows:
        if row.id in seen or not adjacency[row.id]:
            continue
        component: set[uuid.UUID] = set()
        stack = [row.id]
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current] - component)
        seen |= component

        members = sorted((by_id[i] for i in component), key=lambda m: m.created_at)
        chains.append(Chain(chain_id=str(members[0].id), memories=members))

    chains.sort(key=lambda c: c.memories[-1].created_at, reverse=True)
    return chains


async def get_chain_for_memory(
    session: AsyncSession, tenant_id: uuid.UUID, memory_id: uuid.UUID
) -> Chain | None:
    for chain in await build_chains(session, tenant_id):
        if any(m.id == memory_id for m in chain.memories):
            return chain
    return None
