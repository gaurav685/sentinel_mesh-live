"""BM25 keyword scoring — verbatim port of `core.py`'s `BM25` class.

Pure Python/numpy, no store dependency, so nothing here needed adapting for
Postgres/Chroma. Real corpus-level IDF (not the v1-hardcoded `idf=1.0` bug
core.py's own docstring calls out), computed over whatever candidate set the
caller passes in — `app/memory/store.py`'s `vector_search()` results are the
intended candidate set for the hybrid retrieval `nl_query.py` will do later
(60% vector similarity + 40% BM25, per core.py's `MemoryStore.hybrid_retrieve`).
"""

from __future__ import annotations

import math
import re
from typing import Any

__all__ = ["BM25"]


class BM25:
    """Correct Okapi BM25 with real IDF computed over the full candidate
    corpus, plus standard smoothing to avoid negative IDF for very common
    terms."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"\b\w+\b", text.lower())

    def score_candidates(self, query: str, candidates: list[dict[str, Any]]) -> dict[str, float]:
        """candidates: [{"id": ..., "content": ...}] -> {id: bm25_score (0..1 normalized)}"""
        query_terms = self._tokenize(query)
        if not query_terms or not candidates:
            return {}

        docs = [self._tokenize(c["content"]) for c in candidates]
        n_docs = len(docs)
        avg_dl = sum(len(d) for d in docs) / n_docs

        # --- Real IDF: idf(t) = ln( (N - n_t + 0.5) / (n_t + 0.5) + 1 ) ---
        doc_freq: dict[str, int] = {}
        for term in set(query_terms):
            doc_freq[term] = sum(1 for d in docs if term in d)
        idf = {
            term: math.log((n_docs - n_t + 0.5) / (n_t + 0.5) + 1.0)
            for term, n_t in doc_freq.items()
        }

        scores: dict[str, float] = {}
        for cand, doc_terms in zip(candidates, docs):
            dl = len(doc_terms)
            tf_map: dict[str, int] = {}
            for t in doc_terms:
                tf_map[t] = tf_map.get(t, 0) + 1

            score = 0.0
            for term in query_terms:
                tf = tf_map.get(term, 0)
                if tf == 0:
                    continue
                term_idf = idf.get(term, 0.0)
                tf_norm = (tf * (self.k1 + 1)) / (
                    tf + self.k1 * (1 - self.b + self.b * dl / avg_dl)
                )
                score += term_idf * tf_norm
            scores[str(cand["id"])] = score

        max_score = max(scores.values(), default=1.0)
        if max_score > 0:
            scores = {k: v / max_score for k, v in scores.items()}
        return scores
