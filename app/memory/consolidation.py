"""Consolidation: near-duplicate merging + pattern extraction, ported from
core.py's `cluster_memories` / `deduplicate` / `extract_patterns`.

Adapted for Postgres+Chroma (core.py operated on an in-memory dict):
  - `cluster_memories` (KMeans + silhouette model selection) is untouched —
    a pure function over an embeddings array, no store dependency.
  - `deduplicate` / `extract_patterns` both call `store.reindex_tenant()`
    first, then pull the freshly-written embeddings back from Chroma via
    `store.get_embeddings()`. This is deliberate, not incidental: between
    refits, `store.add()` embeds a new memory against whatever vocabulary
    was fitted at the *last* refit (see `embedder.py`'s batch-refit
    trade-off) — fine for keeping writes cheap, but a real problem for
    clustering, where a small or stale vocabulary makes unrelated memories
    collapse to near-identical (or all-zero, fully out-of-vocabulary)
    vectors. Consolidation is a periodic job, not a per-write path, so it
    can afford the O(n) refit cost every time it runs, in exchange for
    clustering against the full, current vocabulary instead of whatever was
    fitted several writes ago.
  - `deduplicate`'s side effects (archive the losing duplicate, credit its
    `retrieval_count` to the keeper) now go through a real Postgres commit
    plus `MemoryStore.set_status` (Chroma metadata update), not an
    in-memory mutation.

Intended to run as a periodic job (APScheduler, once `app/main.py` exists),
not on every write — same rationale as the embedder's batch-refit schedule.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.memory_models import MemoryRecordRow
from app.memory.store import MemoryStore

__all__ = ["cluster_memories", "deduplicate", "extract_patterns"]


def cluster_memories(embeddings: np.ndarray, min_k: int = 2, max_k: int = 8) -> np.ndarray:
    """Returns cluster labels. Falls back to a single cluster if there
    isn't enough data or variance to support >=2 clusters. Verbatim port —
    pure numpy/sklearn, no store dependency."""
    n = embeddings.shape[0]
    if n < min_k * 2:
        return np.zeros(n, dtype=int)

    best_score, best_labels = -1.0, None
    for k in range(min_k, min(max_k, n - 1) + 1):
        try:
            km = KMeans(n_clusters=k, n_init=10, random_state=42)
            labels = km.fit_predict(embeddings)
            if len(set(labels)) < 2:
                continue
            score = silhouette_score(embeddings, labels)
            if score > best_score:
                best_score, best_labels = score, labels
        except Exception:  # noqa: BLE001 - a degenerate k (e.g. all-identical points) is expected, not fatal
            continue

    if best_labels is None:
        return np.zeros(n, dtype=int)
    return best_labels


async def deduplicate(
    store: MemoryStore, session: AsyncSession, tenant_id: uuid.UUID, cosine_threshold: float = 0.94
) -> dict[str, Any]:
    """Merge near-duplicate memories by real cosine similarity of their
    cached embeddings. The highest-`importance_score` member of each
    near-duplicate group is kept; the rest are archived and their
    `retrieval_count` is credited to the keeper."""
    active = (
        await session.execute(
            select(MemoryRecordRow).where(
                MemoryRecordRow.tenant_id == tenant_id, MemoryRecordRow.is_archived.is_(False)
            )
        )
    ).scalars().all()
    if len(active) < 2:
        return {"processed": len(active), "merged": 0}

    await store.reindex_tenant(session, tenant_id)
    embedding_by_id = await store.get_embeddings([row.id for row in active])
    comparable = [row for row in active if row.id in embedding_by_id]
    if len(comparable) < 2:
        return {"processed": len(active), "merged": 0}

    embeddings = np.array([embedding_by_id[row.id] for row in comparable])
    sims = cosine_similarity(embeddings)

    merged = 0
    seen: set[uuid.UUID] = set()
    for i, row_a in enumerate(comparable):
        if row_a.id in seen:
            continue
        group = [row_a]
        for j, row_b in enumerate(comparable):
            if i == j or row_b.id in seen:
                continue
            if sims[i, j] >= cosine_threshold:
                group.append(row_b)
                seen.add(row_b.id)
        if len(group) > 1:
            keeper = max(group, key=lambda r: r.importance_score)
            for dup in group:
                if dup.id != keeper.id:
                    keeper.retrieval_count += dup.retrieval_count
                    dup.is_archived = True
                    merged += 1
            seen.add(keeper.id)

    await session.commit()

    for row in comparable:
        if row.is_archived:
            await store.set_status(session, row.id, archived=True)

    return {"processed": len(active), "merged": merged}


async def extract_patterns(
    store: MemoryStore, session: AsyncSession, tenant_id: uuid.UUID, min_cluster_size: int = 3
) -> list[dict[str, Any]]:
    """Cluster episodic memories with KMeans + silhouette selection; each
    sufficiently large cluster becomes a candidate semantic 'pattern'.
    Read-only — persisting a pattern as its own memory record is the
    caller's decision (`ingest.py`), not this function's."""
    episodics = (
        await session.execute(
            select(MemoryRecordRow).where(
                MemoryRecordRow.tenant_id == tenant_id,
                MemoryRecordRow.memory_type == "episodic",
                MemoryRecordRow.is_archived.is_(False),
            )
        )
    ).scalars().all()
    if len(episodics) < min_cluster_size:
        return []

    await store.reindex_tenant(session, tenant_id)
    embedding_by_id = await store.get_embeddings([row.id for row in episodics])
    comparable = [row for row in episodics if row.id in embedding_by_id]
    if len(comparable) < min_cluster_size:
        return []

    embeddings = np.array([embedding_by_id[row.id] for row in comparable])
    labels = cluster_memories(embeddings)

    patterns: list[dict[str, Any]] = []
    for label in set(labels):
        members = [comparable[i] for i in range(len(comparable)) if labels[i] == label]
        if len(members) < min_cluster_size:
            continue
        # Concept = top shared words across the cluster (same crude
        # extraction core.py used — not the fitted TF-IDF vocabulary).
        words: dict[str, int] = {}
        for row in members:
            for w in re.findall(r"\b\w{5,}\b", row.content.lower()):
                words[w] = words.get(w, 0) + 1
        top_terms = sorted(words.items(), key=lambda x: -x[1])[:5]
        concept = " ".join(w for w, _ in top_terms)
        patterns.append(
            {
                "concept": concept,
                "member_count": len(members),
                "member_ids": [row.id for row in members],
                "confidence": min(1.0, 0.6 + 0.05 * len(members)),
            }
        )
    return patterns
