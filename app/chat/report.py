"""Chain narrative reports -- reuses `app/chat/llm.py`'s LLM call (the
same path `query.py` uses for chat answers), applied to an entire attack
chain (`app/detection/chains.py`) instead of a retrieval result.

Same non-fabrication posture as `query.py`, made even more explicit here
because a *narrative* is easier to mistake for a verified finding than a
short chat answer: the prompt lists every real incident in the chain, in
real chronological order, and instructs the model to describe only what's
given. The API response and the UI both carry an explicit label —
"LLM-generated summary — verify against raw incident data" — the same
evidence/inference distinction the real SentinelMesh repo's reporting
layer draws (a narrative is inference over evidence, never presented as
evidence itself).
"""

from __future__ import annotations

from app.chat.llm import call_llm
from app.detection.chains import Chain

__all__ = ["REPORT_DISCLAIMER", "generate_chain_report"]

REPORT_DISCLAIMER = "LLM-generated summary — verify against raw incident data"

_SYSTEM_PROMPT = (
    "You are a security analyst assistant writing a short incident-chain report. "
    "You will be given a real, ordered list of incidents that belong to one attack "
    "chain. Describe what happened, in chronological order, using ONLY the "
    "incidents given -- never state or imply a detail, technique, or incident not "
    "present in the list. Cite each incident's exact incident_id when you refer to "
    "it. Keep it to a few sentences, chronological, factual."
)


def _format_chain(chain: Chain) -> str:
    lines = []
    for i, memory in enumerate(chain.memories, start=1):
        lines.append(
            f"{i}. incident_id={memory.id} at {memory.created_at.isoformat()} "
            f"(importance={memory.importance_score:.2f}): {memory.content}"
        )
    return "\n".join(lines)


async def generate_chain_report(chain: Chain) -> str:
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"CHAIN ({chain.length} incidents):\n{_format_chain(chain)}"},
    ]
    return await call_llm(messages)
