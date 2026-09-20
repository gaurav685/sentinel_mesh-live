from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.memory.decay import composite_score, decay_score, frequency_score, recency_score


def test_recency_score_decreases_with_age():
    now = datetime.now(timezone.utc)
    fresh = recency_score(now)
    older = recency_score(now - timedelta(days=30))
    oldest = recency_score(now - timedelta(days=90))
    assert fresh > older > oldest
    assert 0.0 <= oldest <= older <= fresh <= 1.0


def test_recency_score_uses_last_retrieved_at_when_present():
    now = datetime.now(timezone.utc)
    created_long_ago = now - timedelta(days=90)
    # Retrieved recently -> should score as fresh as `now`, not as stale as
    # the 90-day-old creation date.
    reinforced = recency_score(created_long_ago, last_retrieved_at=now)
    never_retrieved = recency_score(created_long_ago)
    assert reinforced > never_retrieved
    assert reinforced == recency_score(now)


def test_frequency_score_monotonic_and_bounded():
    assert frequency_score(0) == 0.0
    low = frequency_score(1)
    high = frequency_score(50)
    assert 0.0 < low < high <= 1.0


def test_decay_score_reinforcement_slows_forgetting():
    """Ebbinghaus curve: a memory retrieved many times should decay slower
    (higher retention score at the same age) than one never retrieved."""
    created = datetime.now(timezone.utc) - timedelta(days=45)
    never_retrieved = decay_score(created, retrieval_count=0, importance=0.5)
    often_retrieved = decay_score(created, retrieval_count=20, importance=0.5)
    assert often_retrieved > never_retrieved
    assert 0.0 <= never_retrieved <= 1.0
    assert 0.0 <= often_retrieved <= 1.0


def test_decay_score_importance_slows_forgetting():
    created = datetime.now(timezone.utc) - timedelta(days=45)
    low_importance = decay_score(created, retrieval_count=0, importance=0.1)
    high_importance = decay_score(created, retrieval_count=0, importance=0.9)
    assert high_importance > low_importance


def test_composite_score_bounded_and_weighted():
    # All inputs maxed -> composite must hit 1.0 exactly (weights sum to 1.0).
    assert composite_score(1.0, 1.0, 1.0, 1.0, 1.0) == 1.0
    assert composite_score(0.0, 0.0, 0.0, 0.0, 0.0) == 0.0

    # Relevance carries the highest weight (0.35) -> raising only relevance
    # must move the composite more than raising only frequency (0.15).
    base = composite_score(0.5, 0.5, 0.5, 0.5, 0.5)
    relevance_up = composite_score(0.5, 1.0, 0.5, 0.5, 0.5)
    frequency_up = composite_score(0.5, 0.5, 1.0, 0.5, 0.5)
    assert (relevance_up - base) > (frequency_up - base)
