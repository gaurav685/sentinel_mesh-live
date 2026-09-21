# Public Deployment Decision

Chosen during this pass, verified against live product pages/pricing at the
time (2026-09), not assumed from stale knowledge — see the research done in
conversation for sources. Fly.io was evaluated and rejected: its free tier
is effectively gone (trial credit only as of 2026), which would have made
it a paid choice, not a free one.

| Component | Provider | Service | Reason |
|---|---|---|---|
| Frontend | Vercel | Hobby (free) plan, Next.js native | Zero-config Next.js hosting, automatic HTTPS, generous free tier for a non-commercial demo |
| API | Render | Free Web Service, Docker runtime | Docker-native, free HTTPS, no card required; free tier has no persistent disk, which this app's boot-time-Chroma-reindex-from-Postgres design already accounts for |
| Replay/worker | — | Run on demand, locally, against hosted Redis | Explicit rule (Phase 11/12): replay must never run continuously in production |
| PostgreSQL | Neon | Free tier | No card required, real Postgres (not a toy), 0.5GB/100 CU-hours free, matches this app's existing `asyncpg` driver path exactly |
| Redis | Upstash | Free tier | Free, TLS connection string (`rediss://`) works with `redis-py`'s existing `from_url()` call with zero code change |
| Graph | — | Not deployed | Confirmed in the audit: this codebase never actually integrates a graph store; nothing to provision |
| LLM | Groq | Existing key, unchanged | Already working from the local hardening pass |

No provider's capability was asserted without checking its current,
real product page/pricing during this session (search results with sources
were reviewed in conversation before any of these were chosen).
