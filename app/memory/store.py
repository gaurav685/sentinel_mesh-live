"""Chroma (vector index) + Postgres (durable rows) bridge for the memory
layer. Replaces core.py's `MemoryStore` (an in-memory dict) with a
persistent, restart-safe backing store, using the same `TfidfEmbedder` from
`app/memory/embedder.py` unchanged.

Division of duties (README: "Store substitutions"):
  - **Postgres** (`MemoryRecordRow`) is the source of truth. Every field on
    `core.py`'s `Memory` dataclass lives here.
  - **Chroma** holds only what vector search needs: `(id, embedding,
    content, a few filter metadata fields)`. It runs embedded, on local
    disk (`app/config.py: chroma_persist_dir`) — on Railway/Render a
    redeploy can reset that disk, so Chroma is treated as a rebuildable
    cache, never as the only copy of anything. `rebuild_if_empty()` below
    re-populates it from Postgres.

Refit correctness: `TfidfEmbedder`'s vocabulary-to-dimension mapping can
change entirely on every refit (which term lands at which vector index is a
function of the *current* fitted vocabulary, not additive across fits). So
whenever a refit happens, every row in that tenant's corpus needs its
embedding recomputed and re-upserted into Chroma -- not just the newly
added one. This mirrors `VectorStore._refit_and_embed_all` in embedder.py,
against Postgres+Chroma instead of an in-memory dict.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.memory_models import MemoryRecordRow
from app.memory.embedder import TfidfEmbedder

__all__ = ["MemoryStore"]

_ACTIVE_FILTER_KEYS = ("is_archived", "is_poisoned")


class MemoryStore:
    def __init__(self, chroma_client, embedder: TfidfEmbedder, *, collection_name: str = "memories") -> None:
        self._embedder = embedder
        self._collection = chroma_client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"}
        )

    # -- writes ---------------------------------------------------------

    async def add(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        content: str,
        memory_type: str = "episodic",
        importance: float = 0.5,
        emotional: float = 0.0,
        tags: list[str] | None = None,
    ) -> MemoryRecordRow:
        row = MemoryRecordRow(
            tenant_id=tenant_id,
            content=content,
            memory_type=memory_type,
            importance_score=importance,
            emotional_score=emotional,
            tags=tags or [],
        )
        session.add(row)
        await session.flush()  # assigns row.id, row.created_at

        if self._embedder.should_refit():
            await self.reindex_tenant(session, tenant_id)
        else:
            embedding = self._embedder.transform_only([content])[0]
            self._embedder.note_pending()
            await self._upsert_one(row, embedding)

        await session.commit()
        return row

    async def mark_retrieved(self, session: AsyncSession, memory_id: uuid.UUID) -> None:
        """Ebbinghaus reinforcement: retrieval slows decay (core.py
        `hybrid_retrieve`'s side effect, ported as an explicit call since
        this store has no in-process retrieval loop of its own)."""
        row = await session.get(MemoryRecordRow, memory_id)
        if row is None:
            return
        row.retrieval_count += 1
        row.last_retrieved_at = datetime.now(timezone.utc)
        await session.commit()

    async def set_status(
        self, session: AsyncSession, memory_id: uuid.UUID, *, archived: bool | None = None, poisoned: bool | None = None
    ) -> None:
        row = await session.get(MemoryRecordRow, memory_id)
        if row is None:
            return
        if archived is not None:
            row.is_archived = archived
        if poisoned is not None:
            row.is_poisoned = poisoned
        await session.commit()
        # Metadata-only change: no embedding recompute needed, Chroma supports
        # updating metadata in place.
        await asyncio.to_thread(
            self._collection.update,
            ids=[str(row.id)],
            metadatas=[
                {
                    "tenant_id": str(row.tenant_id),
                    "memory_type": row.memory_type,
                    "is_archived": row.is_archived,
                    "is_poisoned": row.is_poisoned,
                }
            ],
        )

    # -- reads ------------------------------------------------------------

    async def get(self, session: AsyncSession, memory_id: uuid.UUID) -> MemoryRecordRow | None:
        return await session.get(MemoryRecordRow, memory_id)

    async def list_recent(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        *,
        memory_type: str | None = None,
        limit: int = 100,
        include_archived: bool = False,
    ) -> list[MemoryRecordRow]:
        """Postgres-only, chronological listing — for the frontend's alert
        feed and relationship graph, neither of which is a similarity
        search. Deliberately bypasses Chroma entirely."""
        stmt = select(MemoryRecordRow).where(MemoryRecordRow.tenant_id == tenant_id)
        if memory_type is not None:
            stmt = stmt.where(MemoryRecordRow.memory_type == memory_type)
        if not include_archived:
            stmt = stmt.where(MemoryRecordRow.is_archived.is_(False))
        stmt = stmt.order_by(MemoryRecordRow.created_at.desc()).limit(limit)
        return (await session.execute(stmt)).scalars().all()

    async def vector_search(
        self,
        query: str,
        *,
        tenant_id: uuid.UUID,
        top_k: int = 10,
        include_inactive: bool = False,
        memory_type: str | None = None,
    ) -> list[tuple[uuid.UUID, float, str]]:
        """Returns `(memory_id, cosine_similarity, content)`, most similar
        first. Chroma's cosine space returns *distance* (`1 - similarity`);
        converted back to similarity here so callers see the same 0..1
        scale core.py's `VectorStore.search` used.

        `memory_type`, when given, restricts the search to that type only
        (e.g. `app/detection/consumer.py` searching only `"incident"`
        memories before linking a new one to a past occurrence, rather
        than matching against unrelated episodic/preference memories)."""
        if not self._embedder.is_fitted:
            return []

        clauses: list[dict] = [{"tenant_id": str(tenant_id)}]
        if not include_inactive:
            clauses += [{"is_archived": False}, {"is_poisoned": False}]
        if memory_type is not None:
            clauses.append({"memory_type": memory_type})
        where: dict = clauses[0] if len(clauses) == 1 else {"$and": clauses}

        query_embedding = self._embedder.transform_only([query])[0]
        result = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=[query_embedding.tolist()],
            n_results=top_k,
            where=where,
        )
        ids = result["ids"][0] if result["ids"] else []
        distances = result["distances"][0] if result["distances"] else []
        documents = result["documents"][0] if result["documents"] else []
        return [
            (uuid.UUID(rid), 1.0 - dist, doc)
            for rid, dist, doc in zip(ids, distances, documents)
        ]

    async def get_embeddings(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[float]]:
        """Bulk-reads cached embeddings back out of Chroma, keyed by memory
        id. Used by `consolidation.py` (dedup/pattern extraction) so it
        doesn't need to recompute embeddings that are already indexed and
        consistent with the embedder's current vocabulary (see the refit-
        correctness note in the module docstring)."""
        if not ids:
            return {}
        result = await asyncio.to_thread(
            self._collection.get, ids=[str(i) for i in ids], include=["embeddings"]
        )
        return {uuid.UUID(rid): emb for rid, emb in zip(result["ids"], result["embeddings"])}

    # -- rebuild ------------------------------------------------------------

    async def rebuild_if_empty(self, session: AsyncSession, tenant_id: uuid.UUID) -> int:
        """Startup hook: if Chroma's on-disk index was reset (free-tier
        redeploy), repopulate it from Postgres, the durable source of
        truth. Returns the number of rows reindexed; 0 if the index already
        had data (no-op)."""
        existing = await asyncio.to_thread(
            self._collection.get, where={"tenant_id": str(tenant_id)}, limit=1
        )
        if existing["ids"]:
            return 0
        return await self.reindex_tenant(session, tenant_id)

    async def reindex_tenant(self, session: AsyncSession, tenant_id: uuid.UUID) -> int:
        """Full O(n) refit + re-embed + bulk re-upsert for one tenant's
        corpus. Runs on the embedder's refit schedule, on a cold Chroma
        index, or explicitly before consolidation (`consolidation.py` calls
        this first so dedup/pattern-extraction never cluster on a stale,
        partial vocabulary) — never on every `add()`. Public: called from
        outside this module, not just internally."""
        rows = (
            await session.execute(select(MemoryRecordRow).where(MemoryRecordRow.tenant_id == tenant_id))
        ).scalars().all()
        if not rows:
            return 0

        contents = [row.content for row in rows]
        self._embedder.fit(contents)
        embeddings = self._embedder.transform_only(contents)

        await asyncio.to_thread(
            self._collection.upsert,
            ids=[str(row.id) for row in rows],
            embeddings=[emb.tolist() for emb in embeddings],
            documents=contents,
            metadatas=[
                {
                    "tenant_id": str(row.tenant_id),
                    "memory_type": row.memory_type,
                    "is_archived": row.is_archived,
                    "is_poisoned": row.is_poisoned,
                }
                for row in rows
            ],
        )
        return len(rows)

    async def _upsert_one(self, row: MemoryRecordRow, embedding) -> None:
        await asyncio.to_thread(
            self._collection.upsert,
            ids=[str(row.id)],
            embeddings=[embedding.tolist()],
            documents=[row.content],
            metadatas=[
                {
                    "tenant_id": str(row.tenant_id),
                    "memory_type": row.memory_type,
                    "is_archived": row.is_archived,
                    "is_poisoned": row.is_poisoned,
                }
            ],
        )
