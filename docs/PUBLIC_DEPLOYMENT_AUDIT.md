# Public Deployment Audit

Phase 0 output for the public-hosting pass. Builds on `docs/DEPLOYMENT_AUDIT.md`
(the local Docker audit) — only what's new or resolved for a public, hosted
deployment is recorded here, not repeated wholesale.

## Current deployment architecture (as of this pass)

- `api` — Docker image (`Dockerfile`), deployed as a Render free Web Service.
- `frontend` — Next.js, deployed to Vercel (Hobby), built directly from
  `frontend/` via Vercel's own Next.js build pipeline (not the repo's
  `frontend/Dockerfile`, which stays for self-hosted/Docker use).
- `replay` — not deployed as a standing service anywhere; run on demand from
  a local machine against the hosted Redis, exactly as designed (Phase 11/12
  rule: replay must never run continuously in production).

## Docker images

Unchanged from the local pass: `Dockerfile` (api), `Dockerfile.replay`
(replay). Render builds `Dockerfile` directly from the GitHub repo on push.

## Required services (now all real, hosted)

| Service | Provider | Resolved in this pass |
|---|---|---|
| Postgres | Neon | Project `solitary-brook-34616928`, branch `production` |
| Redis | Upstash | `amused-crow-287615.upstash.io`, TLS (`rediss://`) |
| LLM | Groq | Already had a key; unchanged |
| Graph (AuraDB) | — | Confirmed not integrated in this codebase; nothing to deploy |

## Environment variables (names only, set on Render/Vercel, not in git)

Render (`api`): `SML_ENVIRONMENT`, `SML_DATABASE_URL`, `SML_REDIS_URL`,
`SML_LLM_API_KEY`, `SML_LLM_API_BASE`, `SML_LLM_MODEL`, `SML_CORS_ORIGINS`.
Vercel (`frontend`, build-time): `NEXT_PUBLIC_API_BASE_URL`,
`NEXT_PUBLIC_DEMO_TENANT_ID`.

## Persistent volumes

None on the API container itself — Render's free tier has no attached disk.
Not a gap: Postgres (Neon) is the durable store, and Chroma is already
architected as a rebuildable cache reindexed from Postgres at every boot
(confirmed working live during the local Docker pass). This is the exact
scenario that design decision was made for.

## Ports

Render terminates HTTPS and proxies to the container's `$PORT` (the app
already reads `$PORT` via `Procfile`'s `uvicorn` command — no change needed).
Vercel terminates HTTPS for the frontend natively. Neon and Upstash are only
reachable over their own TLS endpoints (5432/6379-equivalent), never exposed
by this deployment directly — the app connects out to them, nothing connects
in.

## Health checks

`/health`, `/live`, `/ready` — all already built (prior hardening pass),
all verified live against the public Render URL in this pass.

## Startup / shutdown

Startup: `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (`Procfile`,
unchanged). Shutdown: Render sends SIGTERM on redeploy/stop; the app's
existing lifespan shutdown path (scheduler shutdown, consumer
`request_stop()`, engine dispose) was already built and verified in the
local Docker pass — confirmed again here indirectly via the CORS-change
redeploy, which preserved all data with no error.

## Known deployment limitations (public-hosting-specific)

- Render free tier cold-starts after 15 minutes of inactivity — the first
  request after idle will be slow. Not tested precisely in this pass
  (no controlled idle-then-hit timing was run); documented as a known
  characteristic of the tier, not measured.
- No custom domain — using Render's and Vercel's provider-generated HTTPS
  URLs.
- Single Render instance, free tier — no horizontal scaling story (matches
  the existing single-consumer-identity architecture note in
  `docs/DEPLOYMENT.md`).
