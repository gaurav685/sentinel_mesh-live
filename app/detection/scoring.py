"""Composite threat score per incident. Exact formula, stated so it can be
argued with -- same transparency standard as the real SentinelMesh repo's
benchmark section, not a black box.

    threat_score = clamp(
        0.5 * confidence
      + 0.2 * recurrence_factor
      + 0.3 * technique_weight,
      0.0, 1.0
    )

Where:
  - **confidence** = the real isolation-forest `normalized_score`
    (`app/detection/model.py`) for this detection, already 0..1. Weighted
    highest (0.5) because it's the only term backed by a trained model;
    the other two are heuristics layered on top of it.
  - **recurrence_factor** = `log1p(chain_length - 1) / log1p(MAX_RECURRENCE)`,
    clamped to 1.0. `chain_length` is the real number of incidents in this
    one's attack chain (`app/detection/chains.py`; 1 for a standalone
    incident, giving recurrence_factor=0). Same `log1p` diminishing-returns
    shape as `app/memory/decay.py`'s `frequency_score` -- reused
    deliberately, not a new formula shape invented for this one score.
    `MAX_RECURRENCE=10`: the factor saturates at 10 linked occurrences: a
    heuristic ceiling, not a calibrated one, same status as every other
    threshold in this project.
  - **technique_weight** = a fixed severity prior per MITRE category
    (`app/mitre/catalog.py`): DoS (T1498)=0.9, Brute Force (T1110)=0.8,
    Probe (T1046)=0.5, unmapped=0.6. Rationale: in standard SOC triage,
    availability/credential-impact techniques outrank reconnaissance:
    Probe is "someone is looking," DoS/Brute-Force are "someone is
    acting." An *unmapped* anomaly (the heuristic found no confident rule
    match) gets 0.6, not the Probe floor -- unmapped means genuinely
    uncertain, not "known to be low-severity," and treating it as
    low-severity by default would be the exact kind of quiet
    under-reporting this project's honesty rules exist to prevent.
"""

from __future__ import annotations

import math

from app.mitre.catalog import Technique

__all__ = ["MAX_RECURRENCE", "TECHNIQUE_WEIGHTS", "UNMAPPED_WEIGHT", "compute_threat_score"]

MAX_RECURRENCE = 10

TECHNIQUE_WEIGHTS: dict[str, float] = {
    "T1498": 0.9,  # Network Denial of Service
    "T1110": 0.8,  # Brute Force
    "T1046": 0.5,  # Network Service Discovery
}
UNMAPPED_WEIGHT = 0.6

_W_CONFIDENCE = 0.5
_W_RECURRENCE = 0.2
_W_TECHNIQUE = 0.3


def compute_threat_score(
    *, confidence: float, chain_length: int, technique: Technique | None
) -> float:
    # Denominator is log1p(MAX_RECURRENCE - 1), not log1p(MAX_RECURRENCE),
    # so the factor reaches exactly 1.0 AT chain_length == MAX_RECURRENCE
    # (the docstring's "saturates at 10 linked occurrences" claim) rather
    # than only approaching 1.0 there and truly saturating one step later.
    recurrence_factor = min(
        1.0, math.log1p(max(0, chain_length - 1)) / math.log1p(MAX_RECURRENCE - 1)
    )
    technique_weight = TECHNIQUE_WEIGHTS.get(technique.id, UNMAPPED_WEIGHT) if technique else UNMAPPED_WEIGHT

    score = _W_CONFIDENCE * confidence + _W_RECURRENCE * recurrence_factor + _W_TECHNIQUE * technique_weight
    return max(0.0, min(1.0, score))
