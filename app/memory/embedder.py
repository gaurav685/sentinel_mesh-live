"""Embedding / vector store, fixed for batch-refit (see MemoryForge core.py v2).

core.py's `VectorStore.add()` and `.search()` each called
`TfidfEmbedder.fit()` on the *entire* corpus on every single call — an O(n)
vocabulary refit plus an O(n) re-embed of every existing record, on every
insert and every query. For n inserts that is O(n^2) total, and it makes
`add()` measurably slower as the corpus grows, which is fatal for a live
dashboard ingesting a steady stream of detections.

Fix: refit the vocabulary on a schedule (`refit_every_n` new documents, or
`refit_interval_seconds` elapsed, whichever comes first) instead of on every
call. Between refits, a new document is embedded with `transform()` against
the *currently fitted* vocabulary — cost bounded by `max_features`, not by
corpus size. `search()` no longer refits or re-embeds at all: it transforms
the query against the current vocabulary and compares to each record's
already-computed (cached) embedding.

Trade-off, accepted deliberately: a term that first appears after the last
refit is out-of-vocabulary until the next refit and is silently dropped by
scikit-learn's `transform()` (it just contributes zero across all features).
Recall for very fresh terms is slightly stale between refits; this is the
price of not paying O(n) on every insert. `refit_every_n=50` /
`refit_interval_seconds=60` bounds the staleness to at most one refit cycle.

Production embedder swap point (documented, not wired up): this class's
interface (`fit(corpus)`, `transform_only(texts) -> np.ndarray`) is exactly
what a `sentence-transformers` wrapper would also implement — swapping in
real embeddings later is a one-line change (`self._embedder =
SentenceTransformerEmbedder()`), nothing else in `VectorStore` changes.
Deliberately NOT done here: `sentence-transformers` pulls in `torch`, whose
installed size and runtime RAM footprint (typically several hundred MB just
for the base model) is a real risk against Railway/Render free-tier memory
limits. Staying on scikit-learn's `TfidfVectorizer` keeps this dependency-
light enough to fit a free-tier dyno.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

__all__ = ["TfidfEmbedder", "VectorRecord", "VectorStore"]


class TfidfEmbedder:
    """Same public shape a `sentence-transformers` wrapper would have
    (`fit`, `transform_only`), so `VectorStore` doesn't care which backs it.
    """

    def __init__(
        self,
        *,
        refit_every_n: int = 50,
        refit_interval_seconds: float = 60.0,
        max_features: int = 4096,
    ) -> None:
        self._vec: TfidfVectorizer | None = None
        self.refit_every_n = refit_every_n
        self.refit_interval_seconds = refit_interval_seconds
        self._max_features = max_features
        self._pending_since_refit = 0
        self._last_refit_at = 0.0
        self.refit_count = 0

    @property
    def is_fitted(self) -> bool:
        return self._vec is not None

    def fit(self, corpus: list[str]) -> None:
        """Full vocabulary refit over the given corpus. O(n) — call only on
        the refit schedule, not on every insert."""
        self._vec = TfidfVectorizer(stop_words="english", max_features=self._max_features)
        self._vec.fit(corpus if corpus else [""])
        self._pending_since_refit = 0
        self._last_refit_at = time.monotonic()
        self.refit_count += 1

    def transform_only(self, texts: list[str]) -> np.ndarray:
        """Embed against the *currently fitted* vocabulary. Never fits.
        Terms not in the current vocabulary are dropped (scored zero across
        all features) — the accepted staleness trade-off documented above.

        Always returns vectors of width `max_features`, zero-padded on the
        right if the actual fitted vocabulary is smaller (common for small
        corpora — `TfidfVectorizer`'s real output width is `len(vocabulary_)`,
        which grows across refits until it hits the cap). This padding is
        required, not cosmetic: Chroma (and pgvector) fix a collection's/
        column's dimensionality on first insert and reject any later vector
        of a different width, so a growing vocabulary would otherwise break
        `store.py` the moment a refit's vocab size changed. Safe because
        every vector compared against another always comes from the *same*
        fitted vectorizer instance (a refit replaces the whole corpus's
        embeddings together, per `store.py`'s reindex-on-refit invariant) —
        the padding is zeros in the same trailing columns on both sides of
        any comparison, so cosine similarity is unaffected."""
        if self._vec is None:
            raise RuntimeError("embedder has no fitted vocabulary yet — call fit() first")
        matrix = self._vec.transform(texts).toarray()
        width = matrix.shape[1]
        if width < self._max_features:
            pad = np.zeros((matrix.shape[0], self._max_features - width), dtype=matrix.dtype)
            matrix = np.hstack([matrix, pad])
        return matrix

    def note_pending(self, n: int = 1) -> None:
        self._pending_since_refit += n

    def should_refit(self) -> bool:
        if self._vec is None:
            return True
        if self._pending_since_refit >= self.refit_every_n:
            return True
        return (time.monotonic() - self._last_refit_at) >= self.refit_interval_seconds


@dataclass
class VectorRecord:
    id: str
    content: str
    embedding: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore:
    """In-memory vector store (swap for ChromaDB/pgvector in production —
    same add/search/delete interface). Batch-refit strategy: see module
    docstring."""

    def __init__(self, embedder: TfidfEmbedder | None = None) -> None:
        self._embedder = embedder or TfidfEmbedder()
        self._records: dict[str, VectorRecord] = {}
        self._order: list[str] = []  # insertion order, parallel to corpus content

    def add(self, memory_id: str, content: str, metadata: dict | None = None) -> None:
        if self._embedder.should_refit():
            embedding = self._refit_and_embed_all(content)
        else:
            embedding = self._embedder.transform_only([content])[0]
            self._embedder.note_pending()

        self._records[memory_id] = VectorRecord(memory_id, content, embedding, metadata or {})
        self._order.append(memory_id)

    def _refit_and_embed_all(self, new_content: str) -> np.ndarray:
        """O(n) — runs only on the refit schedule, not on every add()."""
        contents = [self._records[i].content for i in self._order] + [new_content]
        self._embedder.fit(contents)
        embeddings = self._embedder.transform_only(contents)
        for idx, rid in enumerate(self._order):
            self._records[rid].embedding = embeddings[idx]
        return embeddings[-1]

    def search(self, query: str, top_k: int = 10, where: dict | None = None) -> list[tuple[str, float, str]]:
        """No refit, no re-embed of stored records — compares the query's
        transform-only embedding against each record's cached embedding."""
        if not self._records:
            return []

        ids = self._order
        doc_vecs = np.stack([self._records[i].embedding for i in ids])
        query_vec = self._embedder.transform_only([query])
        sims = cosine_similarity(query_vec, doc_vecs)[0]

        results = []
        for i, sim in zip(ids, sims):
            rec = self._records[i]
            if where and not self._matches(rec.metadata, where):
                continue
            results.append((i, float(sim), rec.content))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def delete(self, memory_id: str) -> None:
        self._records.pop(memory_id, None)
        if memory_id in self._order:
            self._order.remove(memory_id)

    def get_embedding(self, memory_id: str) -> np.ndarray | None:
        rec = self._records.get(memory_id)
        return rec.embedding if rec else None

    @staticmethod
    def _matches(meta: dict, where: dict) -> bool:
        return all(meta.get(k) == v for k, v in where.items())
