"""In-process rate limiting for the LLM-calling routes (`/api/chat`, chain
report generation) — Phase 7's cap on unbounded LLM spend from a public
deployment.

Deliberately not Redis-backed: this project's deployment target is a single
`api` instance (README: "Process topology: 2 deployables, not 19"), so a
per-process sliding window is a correct, sufficient limiter, not a
corner-cut. It would under-count against more than one replica; if this
service is ever horizontally scaled, this needs a shared (Redis-backed)
counter instead -- noted here rather than silently wrong.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.config import settings

__all__ = ["enforce_llm_rate_limit"]

_WINDOW_S = 60.0

# client_ip -> deque of request timestamps within the current window.
_requests: dict[str, deque[float]] = defaultdict(deque)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def enforce_llm_rate_limit(request: Request) -> None:
    limit = settings.llm_rate_limit_per_minute
    if limit <= 0:
        return  # 0/negative disables the limiter -- an explicit opt-out, not a bug.

    key = _client_key(request)
    now = time.monotonic()
    bucket = _requests[key]

    while bucket and now - bucket[0] > _WINDOW_S:
        bucket.popleft()

    if len(bucket) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded: max {limit} requests/minute to LLM-backed endpoints",
        )

    bucket.append(now)
