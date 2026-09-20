"""Natural-language incident chat — the analyst-facing "what has the
system learned" question-answering path.

Retrieval reuses `app/memory/nl_query.hybrid_retrieve` (vector search
narrows candidates, BM25 re-scores, `decay.composite_score` ranks) rather
than calling `MemoryStore.vector_search` a second, separate time —
`hybrid_retrieve` already *is* the existing, already-tested search built
for exactly this question shape ("what happened last time we saw this
signature?" is the same question a chat message asks in free text).

LLM composition is a plain HTTP POST to an OpenAI-compatible
`/chat/completions` endpoint via `httpx`, not the `openai` SDK or any
other vendor package. Every other external dependency in this project
(Redis, Postgres, Chroma) has zero vendor lock-in; the wire format here is
now a de facto standard several providers implement directly (OpenAI
itself, Groq, Together, a local Ollama/vLLM server's openai-compat layer,
...), so the only thing that changes to swap providers is three settings
(`llm_api_base`, `llm_api_key`, `llm_model` — `app/config.py`), not code.

Non-fabrication gate, held to the same standard as every other honesty
rule in this build: if `hybrid_retrieve` returns nothing above
`SIMILARITY_THRESHOLD`, **the LLM is never called at all.** There is no
real context for it to answer from, so refusing to ask the question is
the only guarantee that holds regardless of model behavior — a system
prompt that says "say you don't know" is not the same guarantee, since a
model can still ignore that instruction under generation pressure. When
the LLM *is* called, the system prompt repeats the constraint anyway
(defense in depth), and the returned `matches` list carries the exact
real incident ids/timestamps that were actually retrieved, so a caller
can verify the answer's citations against real data itself rather than
trusting the LLM's citations blindly.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.llm import LLMUnavailable, call_llm
from app.memory.nl_query import RankedMemory, hybrid_retrieve
from app.memory.store import MemoryStore

__all__ = ["SIMILARITY_THRESHOLD", "LLMUnavailable", "answer_chat"]


#: Below this composite score, nothing is treated as relevant enough to
#: answer from. A heuristic threshold, not a calibrated one -- same status
#: as every other similarity cutoff already in this project
#: (`contradiction.py`, `detection/consumer.py`).
SIMILARITY_THRESHOLD = 0.3

_SYSTEM_PROMPT = (
    "You are a security analyst assistant answering questions about past "
    "incidents recorded in the SentinelMesh Live memory store. You may "
    "ONLY use the incidents listed under CONTEXT below -- never state or "
    "imply the existence of any incident, id, timestamp, or detail that is "
    "not present in CONTEXT. Every time you reference an incident, cite "
    "its exact incident_id. If CONTEXT does not contain enough information "
    "to answer the question, say so explicitly instead of guessing."
)


def _format_context(ranked: list[RankedMemory]) -> str:
    lines = []
    for item in ranked:
        row = item.row
        tags = ", ".join(row.tags) if row.tags else "none"
        lines.append(
            f"- incident_id={row.id} created_at={row.created_at.isoformat()} "
            f"memory_type={row.memory_type} importance={row.importance_score:.2f} "
            f"tags=[{tags}]\n  content: {row.content}"
        )
    return "\n".join(lines)


async def answer_chat(
    store: MemoryStore,
    session: AsyncSession,
    query: str,
    *,
    tenant_id: uuid.UUID,
    top_k: int = 5,
) -> dict[str, Any]:
    ranked = await hybrid_retrieve(store, session, query, tenant_id=tenant_id, top_k=top_k)
    relevant = [r for r in ranked if r.composite >= SIMILARITY_THRESHOLD]

    if not relevant:
        return {
            "query": query,
            "answer": "No matching incidents were found in memory for this question.",
            "matches": [],
            "grounded": True,
        }

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"CONTEXT:\n{_format_context(relevant)}\n\nQUESTION: {query}"},
    ]
    answer_text = await call_llm(messages)

    return {
        "query": query,
        "answer": answer_text,
        "matches": [
            {
                "memory_id": str(item.row.id),
                "content": item.row.content,
                "created_at": item.row.created_at.isoformat(),
                "composite_score": round(item.composite, 4),
                "tags": item.row.tags,
            }
            for item in relevant
        ],
        "grounded": True,
    }
