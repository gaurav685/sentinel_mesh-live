"""Ranking engine — verbatim port of `core.py`'s recency/frequency/decay/
composite-score functions. core.py's own comment calls these "unchanged
formulas from v1 — these were already sound"; nothing here needed adapting
for Postgres/Chroma, since they take plain values (`datetime`, `int`,
`float`), not a store dependency.

`decay_score` is the Ebbinghaus forgetting curve used for consolidation
(candidate for archiving) and for "how stale is this incident memory"
display in the SOC UI; `composite_score` is the ranking core.py's
`hybrid_retrieve` sorts by, after combining with `retrieval.BM25` +
`store.MemoryStore.vector_search`'s similarity.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

__all__ = [
    "HALF_LIFE_DAYS",
    "RANK_WEIGHTS",
    "recency_score",
    "frequency_score",
    "decay_score",
    "composite_score",
]

HALF_LIFE_DAYS = 30.0
RANK_WEIGHTS = {
    "recency": 0.25,
    "relevance": 0.35,
    "frequency": 0.15,
    "emotional": 0.10,
    "importance": 0.15,
}


def _days_since(dt: datetime) -> float:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 86400.0


def recency_score(created_at: datetime, last_retrieved_at: datetime | None = None) -> float:
    lam = math.log(2) / HALF_LIFE_DAYS
    ref = last_retrieved_at or created_at
    return math.exp(-lam * _days_since(ref))


def frequency_score(retrieval_count: int, max_count: int = 100) -> float:
    if retrieval_count <= 0:
        return 0.0
    return math.log1p(retrieval_count) / math.log1p(max_count)


def decay_score(created_at: datetime, retrieval_count: int, importance: float) -> float:
    """Ebbinghaus forgetting curve: R = e^(-t/S), S grows with importance +
    reinforcement (each retrieval slows the decay)."""
    base_stability = HALF_LIFE_DAYS * (1 + importance)
    stability = base_stability * (1 + math.log1p(retrieval_count) * 0.5)
    return math.exp(-_days_since(created_at) / stability)


def composite_score(
    recency: float, relevance: float, frequency: float, emotional: float, importance: float
) -> float:
    c = (
        RANK_WEIGHTS["recency"] * recency
        + RANK_WEIGHTS["relevance"] * relevance
        + RANK_WEIGHTS["frequency"] * frequency
        + RANK_WEIGHTS["emotional"] * emotional
        + RANK_WEIGHTS["importance"] * importance
    )
    return max(0.0, min(1.0, c))
