"""The one place this project talks to an LLM. Both `query.py` (chat
answers) and `report.py` (chain narrative reports) call `call_llm` --
extracted here specifically so report.py reuses this HTTP path instead of
re-implementing it, per that feature's own requirement.

Plain HTTP POST to an OpenAI-compatible `/chat/completions` endpoint via
`httpx`, not the `openai` SDK or any other vendor package -- see
`app/config.py`'s `llm_api_base`/`llm_api_key`/`llm_model` for why.
"""

from __future__ import annotations

import logging
import time

import httpx

from app.config import settings

__all__ = ["LLMUnavailable", "call_llm"]

_log = logging.getLogger("sentinelmesh_live.chat.llm")


class LLMUnavailable(RuntimeError):
    """Raised when the configured LLM endpoint is unreachable, misconfigured,
    or returns something unparseable. Never silently swallowed into a
    fabricated or empty-looking success -- the caller (an HTTP route)
    turns this into a real error response."""


async def call_llm(messages: list[dict[str, str]], *, temperature: float = 0.0) -> str:
    if not settings.llm_api_key:
        raise LLMUnavailable("no LLM API key configured (set SML_LLM_API_KEY)")

    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": settings.llm_max_tokens,
    }

    # One retry on a transient network/timeout failure only -- generation
    # is not mutating anything, so a retry can't double-apply an effect.
    # Deliberately NOT retried: any response that reached the provider
    # (handled below via status_code/body checks) -- a 4xx/5xx from the
    # provider itself is a real answer about the request, not a transient
    # failure a second identical request would fix.
    start = time.monotonic()
    last_exc: httpx.HTTPError | None = None
    resp = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
                resp = await client.post(
                    f"{settings.llm_api_base.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                    json=payload,
                )
            break
        except httpx.HTTPError as exc:
            last_exc = exc
    latency_ms = round((time.monotonic() - start) * 1000, 1)

    if resp is None:
        _log.warning("llm_call_failed", extra={"reason": "unreachable", "latency_ms": latency_ms})
        raise LLMUnavailable(f"could not reach LLM endpoint {settings.llm_api_base}: {last_exc}") from last_exc

    if resp.status_code >= 400:
        _log.warning(
            "llm_call_failed",
            extra={"reason": "http_error", "status_code": resp.status_code, "latency_ms": latency_ms},
        )
        raise LLMUnavailable(f"LLM endpoint returned HTTP {resp.status_code}: {resp.text[:300]}")

    body = resp.json()
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        _log.warning("llm_call_failed", extra={"reason": "bad_response_shape", "latency_ms": latency_ms})
        raise LLMUnavailable(f"unexpected LLM response shape: {body!r}") from exc

    _log.info("llm_call_succeeded", extra={"model": settings.llm_model, "latency_ms": latency_ms})
    return content
