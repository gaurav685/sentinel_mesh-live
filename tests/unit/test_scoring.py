from __future__ import annotations

import pytest

from app.detection.scoring import compute_threat_score
from app.mitre.catalog import CATALOG


def test_standalone_unmapped_low_confidence():
    score = compute_threat_score(confidence=0.1, chain_length=1, technique=None)
    # 0.5*0.1 + 0.2*0 + 0.3*0.6 = 0.23
    assert score == pytest.approx(0.23)


def test_dos_technique_scores_higher_than_probe_at_same_confidence():
    dos = compute_threat_score(confidence=0.7, chain_length=1, technique=CATALOG["T1498"])
    probe = compute_threat_score(confidence=0.7, chain_length=1, technique=CATALOG["T1046"])
    assert dos > probe


def test_longer_chain_scores_higher_than_standalone_same_technique():
    standalone = compute_threat_score(confidence=0.7, chain_length=1, technique=CATALOG["T1110"])
    chained = compute_threat_score(confidence=0.7, chain_length=3, technique=CATALOG["T1110"])
    assert chained > standalone


def test_recurrence_factor_saturates_at_max_recurrence():
    at_max = compute_threat_score(confidence=0.5, chain_length=10, technique=None)
    beyond_max = compute_threat_score(confidence=0.5, chain_length=50, technique=None)
    assert at_max == pytest.approx(beyond_max)


def test_score_always_bounded_0_to_1():
    for conf in (0.0, 0.5, 1.0):
        for length in (1, 2, 10, 100):
            for tech in (None, CATALOG["T1498"], CATALOG["T1046"], CATALOG["T1110"]):
                score = compute_threat_score(confidence=conf, chain_length=length, technique=tech)
                assert 0.0 <= score <= 1.0


def test_max_achievable_score_with_current_weights():
    # No technique weight reaches 1.0 (DoS, the highest, is 0.9), so the
    # real achievable ceiling under these weights is 0.97, not 1.0 --
    # clamp(..., 0, 1) is a safety bound, not a claim that 1.0 is reachable.
    score = compute_threat_score(confidence=1.0, chain_length=100, technique=CATALOG["T1498"])
    assert score == pytest.approx(0.97)
