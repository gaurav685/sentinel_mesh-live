"""Negation-scope contradiction / prompt-injection ("poisoning") detector,
ported from core.py's `contradiction_score` / `check_injection` /
`analyze_for_poisoning`.

Still heuristic, not a full NLI model (core.py is explicit about this) —
requires topical overlap *and* a polarity flip or negation-scope mismatch,
rather than firing on any positive/negative word pair anywhere in two
texts.

Real adaptation, not a copy-paste (per plan): `CONTRADICTION_POLARITY_PAIRS`
in core.py was tuned for personal-assistant memories (`love/hate`,
`always/never`). For threat memory, "I love coffee" / "I hate coffee" isn't
the failure mode — "host X is compromised" vs "host X was cleared" is. The
word lists below are rewritten for that domain; `_shares_predicate`,
`_negation_attached`, and `contradiction_score`'s scoring formula are
otherwise unchanged.

`analyze_for_poisoning` is also adapted: core.py mutated `new_memory.
is_poisoned` directly on an in-memory dataclass before it was ever stored.
Here it takes plain `content` and returns an analysis result — whether to
store the memory anyway (marked `is_poisoned=True`) or reject it outright
is `ingest.py`'s call, not this module's (`store.add()` has no
`is_poisoned` parameter of its own for that reason).

Candidate retrieval for the contradiction check uses `store.vector_search`
(vector similarity only) rather than core.py's `hybrid_retrieve` (vector +
BM25) — `nl_query.py`, which will do full hybrid retrieval, doesn't exist
yet. The contradiction algorithm itself only needs *some* semantically
similar candidate set to compare against, not the exact hybrid ranking, so
this is a documented simplification, not a shortcut on the algorithm.
"""

from __future__ import annotations

import re
import uuid

from app.memory.store import MemoryStore

__all__ = [
    "NEGATION_CUES",
    "CONTRADICTION_POLARITY_PAIRS",
    "INJECTION_PATTERNS",
    "contradiction_score",
    "check_injection",
    "analyze_for_poisoning",
]

NEGATION_CUES = {
    "not", "never", "no longer", "don't", "doesn't", "isn't", "wasn't", "can't", "won't", "stopped",
}

# Security-domain polarity pairs (rewritten from core.py's personal-
# preference pairs — love/hate, always/never — which don't apply here).
CONTRADICTION_POLARITY_PAIRS = [
    (["compromised", "infected", "breached", "exploited"], ["clean", "remediated", "cleared", "patched"]),
    (["active", "ongoing", "persistent", "spreading"], ["dormant", "resolved", "closed", "contained"]),
    (["confirmed", "verified", "malicious"], ["false_positive", "false positive", "benign", "legitimate"]),
    (["escalated"], ["deescalated", "downgraded"]),
]


def _shared_predicates(a: str, b: str) -> set[str]:
    """Return every content word (predicate) two sentences both talk
    about, if any — e.g. both mention 'host-42'. Empty = not comparable.

    Bug fixed here vs. an earlier version of this port: returning a single
    arbitrary shared word (`next(iter(shared))`) made negation-scope
    detection non-deterministic — CPython's set iteration order depends on
    hash randomization, which is randomized per process by default, so the
    *same* two sentences could pick a different shared word (and therefore
    fire or not fire on a real negation, e.g. "confirmed" vs. "not
    confirmed") across otherwise-identical runs. `contradiction_score` now
    checks negation-scope against every shared word, not one arbitrary
    pick."""
    a_words = set(re.findall(r"\b\w{4,}\b", a.lower())) - NEGATION_CUES
    b_words = set(re.findall(r"\b\w{4,}\b", b.lower())) - NEGATION_CUES
    return a_words & b_words


def _negation_attached(sentence: str, target_word: str, window: int = 4) -> bool:
    """True if a negation cue appears within `window` tokens of
    target_word — a real negation-SCOPE check, not "anywhere in the
    sentence"."""
    tokens = re.findall(r"\b\w+\b", sentence.lower())
    if target_word not in tokens:
        return False
    idx = tokens.index(target_word)
    lo, hi = max(0, idx - window), min(len(tokens), idx + window + 1)
    nearby = tokens[lo:hi]
    return any(cue in " ".join(nearby) for cue in NEGATION_CUES)


def contradiction_score(content_a: str, content_b: str, semantic_similarity: float) -> tuple[float, str]:
    """Returns (score 0..1, explanation). Fires only when the two memories
    share a concrete predicate/topic word AND that shared word carries
    opposite polarity OR one sentence negates it in the other's scope."""
    a_lower, b_lower = content_a.lower(), content_b.lower()
    shared_words = _shared_predicates(content_a, content_b)

    polarity_flip = 0.0
    for pos_words, neg_words in CONTRADICTION_POLARITY_PAIRS:
        has_pos_a = any(w in a_lower for w in pos_words)
        has_neg_b = any(w in b_lower for w in neg_words)
        has_neg_a = any(w in a_lower for w in neg_words)
        has_pos_b = any(w in b_lower for w in pos_words)
        if (has_pos_a and has_neg_b) or (has_neg_a and has_pos_b):
            polarity_flip = max(polarity_flip, 0.6)

    # Check negation-scope against every shared word, not one arbitrary
    # pick (see `_shared_predicates` docstring for the bug this fixes) —
    # deterministic, and catches a flip on whichever shared word actually
    # carries it even when several words are shared.
    negation_flip = 0.0
    flipped_word: str | None = None
    for word in sorted(shared_words):  # sorted: deterministic `reason` string too
        neg_in_a = _negation_attached(a_lower, word)
        neg_in_b = _negation_attached(b_lower, word)
        if neg_in_a != neg_in_b:
            negation_flip = 0.6
            flipped_word = word
            break

    if not shared_words and polarity_flip == 0.0:
        return 0.0, "no shared topic — not comparable"

    signal = max(polarity_flip, negation_flip)
    score = semantic_similarity * 0.35 + signal * 0.65
    shared_repr = flipped_word or (min(shared_words) if shared_words else None)
    reason = f"shared_topic={shared_repr!r} polarity_flip={polarity_flip > 0} negation_flip={negation_flip > 0}"
    return min(1.0, score), reason


INJECTION_PATTERNS = [
    "ignore previous", "forget everything", "new instructions", "override memory",
    "system prompt", "disregard", "delete all memories", "you are now", "act as",
]


def check_injection(content: str) -> str | None:
    lower = content.lower()
    for pattern in INJECTION_PATTERNS:
        if pattern in lower:
            return f"injection pattern detected: '{pattern}'"
    return None


async def analyze_for_poisoning(
    store: MemoryStore, *, tenant_id: uuid.UUID, content: str, threshold: float = 0.6
) -> dict | None:
    """Screens `content` before it's stored. Returns an analysis dict if it
    should be flagged, `None` if it's clean. Does not mutate anything —
    the caller (`ingest.py`) decides what to do with a flagged memory."""
    inj = check_injection(content)
    if inj:
        return {"type": "injection", "score": 0.95, "reason": inj}

    candidates = await store.vector_search(content, tenant_id=tenant_id, top_k=5)
    for candidate_id, similarity, candidate_content in candidates:
        score, reason = contradiction_score(content, candidate_content, similarity)
        if score >= threshold:
            return {"type": "contradiction", "score": score, "reason": reason, "conflicts_with": candidate_id}
    return None
