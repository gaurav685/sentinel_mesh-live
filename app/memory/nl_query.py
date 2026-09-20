"""Natural-language recall: "what happened last time we saw this
signature?" — the module that combines what the file split separated:
`store.py`'s vector search, `retrieval.py`'s BM25, and `decay.py`'s
ranking, the same 60% vector + 40% BM25 blend `core.py`'s single-class
`MemoryStore.hybrid_retrieve` did.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.memory_models import MemoryRecordRow
from app.memory.decay import composite_score, decay_score, frequency_score, recency_score
from app.memory.retrieval import BM25
from app.memory.store import MemoryStore

__all__ = ["RankedMemory", "hybrid_retrieve", "answer_query"]

_VECTOR_WEIGHT = 0.6
_BM25_WEIGHT = 0.4


@dataclass
class RankedMemory:
    row: MemoryRecordRow
    hybrid_similarity: float
    composite: float


async def hybrid_retrieve(
    store: MemoryStore,
    session: AsyncSession,
    query: str,
    *,
    tenant_id: uuid.UUID,
    top_k: int = 5,
    candidate_pool: int = 20,
    reinforce: bool = True,
) -> list[RankedMemory]:
    """Vector search (`store.py`) narrows the candidate pool, BM25
    (`retrieval.py`) re-scores it on keyword match, `decay.py`'s
    `composite_score` does the final ranking (recency + relevance +
    frequency + emotional + importance). Reinforces every *returned*
    memory by default (Ebbinghaus: retrieval slows forgetting) — same side
    effect core.py's `hybrid_retrieve` had.

    `vector_search`'s default (`include_inactive=False`) already excludes
    archived and poisoned memories, so a memory `ingest.py` flagged as a
    contradiction never surfaces here."""
    vector_hits = await store.vector_search(query, tenant_id=tenant_id, top_k=candidate_pool)
    if not vector_hits:
        return []

    candidates = [{"id": str(memory_id), "content": content} for memory_id, _, content in vector_hits]
    bm25_scores = BM25().score_candidates(query, candidates)

    ranked: list[RankedMemory] = []
    for memory_id, vector_sim, _content in vector_hits:
        row = await store.get(session, memory_id)
        if row is None:  # deleted between the vector search and this fetch
            continue
        bm25_score = bm25_scores.get(str(memory_id), 0.0)
        hybrid_sim = _VECTOR_WEIGHT * vector_sim + _BM25_WEIGHT * bm25_score

        recency = recency_score(row.created_at, row.last_retrieved_at)
        frequency = frequency_score(row.retrieval_count)
        emotional = abs(row.emotional_score)
        composite = composite_score(recency, hybrid_sim, frequency, emotional, row.importance_score)
        ranked.append(RankedMemory(row=row, hybrid_similarity=hybrid_sim, composite=composite))

    ranked.sort(key=lambda r: r.composite, reverse=True)
    top = ranked[:top_k]

    if reinforce:
        for item in top:
            await store.mark_retrieved(session, item.row.id)

    return top


def answer_query(query: str, ranked: list[RankedMemory]) -> dict[str, Any]:
    """Assembles the "what happened last time we saw this signature?"
    answer directly from ranked memories' stored content — no LLM call, no
    generated summary. Grounded and literal by construction: it cannot
    fabricate detail the memories don't actually contain, matching
    SentinelMesh's non-fabrication principle for anything shown to an
    analyst as fact."""
    if not ranked:
        return {"query": query, "answer": "No matching memory found for this query.", "matches": []}

    top = ranked[0]
    last_seen = top.row.last_retrieved_at or top.row.created_at
    staleness = decay_score(top.row.created_at, top.row.retrieval_count, top.row.importance_score)
    answer = (
        f"Most similar past record (last seen {last_seen:%Y-%m-%d}, "
        f"retention {staleness:.2f}): {top.row.content}"
    )

    return {
        "query": query,
        "answer": answer,
        "matches": [
            {
                "memory_id": str(item.row.id),
                "content": item.row.content,
                "memory_type": item.row.memory_type,
                "composite_score": round(item.composite, 4),
                "hybrid_similarity": round(item.hybrid_similarity, 4),
                "created_at": item.row.created_at.isoformat(),
                "last_retrieved_at": (
                    item.row.last_retrieved_at.isoformat() if item.row.last_retrieved_at else None
                ),
            }
            for item in ranked
        ],
    }
