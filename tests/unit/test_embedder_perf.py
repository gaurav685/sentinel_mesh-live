"""Confirms the batch-refit fix: VectorStore.add() latency for a *non-refit*
call does not grow with corpus size. The old core.py behaviour (full
TF-IDF refit + full re-embed of every existing record on every single
add()) is O(n) per call; this test would fail against that implementation
because the last 50 calls would run measurably slower than the first 50.
"""

from __future__ import annotations

import time

from app.memory.embedder import TfidfEmbedder, VectorStore

_TOPICS = [
    "authentication failure burst from",
    "outbound connection to suspicious",
    "isolation forest anomaly score for",
    "lateral movement attempt via",
    "ransomware encryption behavior on",
    "credential stuffing detected against",
    "port scan originating from",
    "privilege escalation attempt on",
    "data exfiltration pattern from",
    "beaconing traffic to domain",
]


def _synthetic_memory(i: int) -> str:
    topic = _TOPICS[i % len(_TOPICS)]
    return f"{topic} host-{i:04d} at intensity {i % 7} seen during incident batch {i // 20}"


def test_add_latency_stays_flat_across_200_inserts() -> None:
    # Large interval so only the count-based trigger (every 50 inserts) fires
    # during this test — keeps the test deterministic, not wall-clock-timing dependent.
    embedder = TfidfEmbedder(refit_every_n=50, refit_interval_seconds=9_999.0)
    store = VectorStore(embedder)

    latencies: list[float] = []
    refit_flags: list[bool] = []

    for i in range(200):
        before = embedder.refit_count
        start = time.perf_counter()
        store.add(f"mem-{i}", _synthetic_memory(i))
        latencies.append(time.perf_counter() - start)
        refit_flags.append(embedder.refit_count != before)

    non_refit_latencies = [lat for lat, was_refit in zip(latencies, refit_flags) if not was_refit]

    # Refits should be infrequent (roughly every 50 inserts), not on every call.
    assert embedder.refit_count <= 6, f"expected ~4-5 refits over 200 inserts, got {embedder.refit_count}"
    assert len(non_refit_latencies) >= 180, "too many calls triggered a refit"

    # Compare early vs late non-refit call cost. Under the fixed implementation
    # this is flat (bounded by vocab size, not corpus size); under the old
    # O(n)-per-call implementation this ratio would be roughly proportional
    # to how much the corpus grew (~20x at n=200 vs n=10).
    early = non_refit_latencies[:40]
    late = non_refit_latencies[-40:]
    early_mean = sum(early) / len(early)
    late_mean = sum(late) / len(late)

    # Generous bound to absorb system noise/GC pauses while still catching a
    # real O(n) regression, which would blow well past this.
    assert late_mean < early_mean * 5 + 0.01, (
        f"non-refit add() latency grew with corpus size: early_mean={early_mean:.6f}s "
        f"late_mean={late_mean:.6f}s (expected roughly flat)"
    )

    # Sanity: 200 inserts (with ~5 O(n) refits mixed in) should still complete
    # quickly on a laptop/CI box — a real O(n^2) implementation would not.
    assert sum(latencies) < 5.0, f"200 inserts took {sum(latencies):.3f}s total, expected well under 5s"


def test_transform_only_output_width_is_stable_across_refits() -> None:
    """Regression test: a Chroma collection (and a pgvector column) fixes
    its dimensionality on the first insert and rejects a later vector of a
    different width. `TfidfVectorizer`'s real output width is
    `len(vocabulary_)`, which grows across refits for a small corpus, so
    without zero-padding to `max_features` this breaks the moment a refit
    changes the vocabulary size."""
    embedder = TfidfEmbedder(max_features=128)
    embedder.fit(["short corpus with few words"])
    narrow = embedder.transform_only(["short corpus"])
    assert narrow.shape[1] == 128

    embedder.fit(
        [
            "a much larger corpus",
            "with many more distinct words than the first fit had",
            "covering a wider vocabulary across several unrelated sentences",
        ]
    )
    wide = embedder.transform_only(["a much larger corpus"])
    assert wide.shape[1] == 128
    assert narrow.shape[1] == wide.shape[1]


def test_search_does_not_refit_or_mutate_embeddings() -> None:
    embedder = TfidfEmbedder(refit_every_n=50, refit_interval_seconds=9_999.0)
    store = VectorStore(embedder)
    for i in range(20):
        store.add(f"mem-{i}", _synthetic_memory(i))

    refit_count_before = embedder.refit_count
    embedding_before = store.get_embedding("mem-0").copy()

    results = store.search("authentication failure", top_k=5)

    assert embedder.refit_count == refit_count_before, "search() must not trigger a refit"
    assert (store.get_embedding("mem-0") == embedding_before).all(), "search() must not mutate stored embeddings"
    assert len(results) == 5
    assert all(isinstance(r[1], float) for r in results)
