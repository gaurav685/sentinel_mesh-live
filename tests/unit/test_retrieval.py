from __future__ import annotations

from app.memory.retrieval import BM25


def test_bm25_uses_real_idf_not_hardcoded_1_0():
    """Regression test for the exact bug core.py's docstring calls out:
    v1 hardcoded idf=1.0 for every term, which made a term that appears in
    every candidate score identically to a rare, distinguishing term. Real
    IDF must rank a document containing a rare term higher than one that
    only matches a common term shared by every candidate."""
    bm25 = BM25()
    candidates = [
        {"id": "common_only", "content": "detection alert detection alert detection alert"},
        {"id": "rare_term", "content": "detection alert exfiltration"},
        {"id": "unrelated", "content": "unrelated content about something else entirely"},
    ]
    # "detection"/"alert" appear in every candidate (df=2/3) -> low IDF.
    # "exfiltration" appears in exactly one -> high IDF, should dominate.
    scores = bm25.score_candidates("exfiltration", candidates)

    assert scores["rare_term"] > 0.0
    assert scores.get("common_only", 0.0) == 0.0  # no query term match at all
    assert scores["rare_term"] > scores.get("unrelated", 0.0)


def test_bm25_scores_normalized_to_unit_range():
    bm25 = BM25()
    candidates = [
        {"id": "a", "content": "ransomware encryption behavior on host"},
        {"id": "b", "content": "ransomware encryption behavior on host repeated multiple times here"},
    ]
    scores = bm25.score_candidates("ransomware encryption", candidates)
    assert scores
    assert max(scores.values()) == 1.0
    assert all(0.0 <= v <= 1.0 for v in scores.values())


def test_bm25_empty_query_or_candidates_returns_empty():
    bm25 = BM25()
    assert bm25.score_candidates("", [{"id": "a", "content": "x"}]) == {}
    assert bm25.score_candidates("query", []) == {}
