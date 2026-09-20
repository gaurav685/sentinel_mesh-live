# Deployment Audit — SentinelMesh Live

Phase 0 reconnaissance. No code changed to produce this document. Every claim
below was verified by reading the actual source in this repo on 2026-09-20 —
nothing here is copied from the session README or from memory of prior work
without re-checking the file.

## 1. Current architecture (as built)

Two long-running processes plus one static frontend build:

- **`api`** — `app/main.py`, a single FastAPI app. Owns: memory HTTP routes,
  chat routes, detection routes, the Redis Streams detection consumer
  (background asyncio task), and an APScheduler job that runs consolidation
  for every tenant on an interval. Started with
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (`Procfile`).
- **`replay`** — `replay/main.py`, a standalone script. Reads NSL-KDD rows,
  publishes to Redis stream `telemetry.raw`. Never imports or calls into
  `api`; the only coupling is the shared Redis stream name and the shared
  `--tenant-id` default. Started with `python -m replay.main` (`Procfile`).
- **`frontend`** — Next.js app (`frontend/`), builds to a standalone Node
  server (`output: "standalone"`) or is deployable to a static/edge host.
  Talks to `api` only over HTTP, via `frontend/lib/api.ts`.

Data stores, all owned by `api` (replay owns none):

- **PostgreSQL** (prod) / **SQLite+aiosqlite** (default, tests/local dev) —
  `app/db/engine.py`, `app/db/session.py`. Source of truth for detections and
  memory records.
- **ChromaDB**, embedded, persistent-on-disk client (`chromadb.PersistentClient`)
  — vector index for the memory layer. Explicitly documented as a rebuildable
  cache, not a source of truth.
- **Redis** — both a message bus (Streams, `telemetry.raw`) and nothing else;
  no Redis-based caching or session state exists.
- **ML artifact on local disk** — `ml/artifacts/isolation_forest_network_flow/v1/`
  (`model.joblib`, 2,018,045 bytes + `metadata.json`, 865 bytes). Loaded by
  `app/detection/model.py`. Not fetched remotely; must ship with the deployed
  artifact/image.

## 2. Runtime dependencies

From `requirements.txt`: `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2,<3`,
`asyncpg` (prod Postgres driver), `aiosqlite` (dev/test), `pydantic`/
`pydantic-settings`, `chromadb>=1.5,<2`, `apscheduler>=3.10,<4`, `redis>=5,<6`,
`scikit-learn==1.9.1` (pinned exact — must match the version the artifact was
trained under, per `app/detection/model.py`'s own docstring), `joblib`,
`numpy`, `httpx` (also used as the LLM HTTP client at runtime, not just for
tests).

Frontend (`frontend/package.json` not re-quoted here, but confirmed present):
Next.js 16, Tailwind v4, Framer Motion, a 2D force-graph library.

External network dependencies at runtime: Postgres (or sqlite file),
Redis, an OpenAI-compatible LLM HTTP endpoint. No other outbound calls exist
in `app/`.

## 3. Environment variables (all `SML_`-prefixed, `app/config.py`)

| Variable | Default | Required in prod? |
|---|---|---|
| `SML_DATABASE_URL` | `sqlite+aiosqlite:///./data/sentinelmesh_live.db` | Yes — must point at hosted Postgres |
| `SML_CHROMA_PERSIST_DIR` | `./data/chroma` | Yes — must be a writable, persistent volume path |
| `SML_CONSOLIDATION_INTERVAL_MINUTES` | `15` | No |
| `SML_REDIS_URL` | `redis://localhost:6379` | Yes — must point at hosted Redis |
| `SML_LLM_API_BASE` | `https://api.openai.com/v1` | No (has default) |
| `SML_LLM_API_KEY` | `None` | Yes — chat/report routes fail cleanly without it (`LLMUnavailable`), but silently absent otherwise |
| `SML_LLM_MODEL` | `gpt-4o-mini` | No (has default) |

Frontend (`NEXT_PUBLIC_*`, baked in at build time, not runtime —
`frontend/Dockerfile`):

| Variable | Default | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | **Must** be set at build time to the deployed API's public URL |
| `NEXT_PUBLIC_DEMO_TENANT_ID` | `00000000-0000-0000-0000-000000000001` | Must match whatever `--tenant-id` replay was run with |

No `CORS_ORIGINS`, `API_BASE_URL`, `ENVIRONMENT`, or `LOG_LEVEL` setting
exists today — see Phase 2 gaps below.

## 4. Localhost / local-machine assumptions found

- `SML_REDIS_URL` default is `redis://localhost:6379` — fine as a default,
  but nothing warns if this is still set in a prod environment where Redis
  is actually elsewhere; `EventBusConsumer.start()`/`EventBusProducer.start()`
  will just hang or fail against an unreachable localhost.
- `NEXT_PUBLIC_API_BASE_URL` defaults to `http://localhost:8000` both in
  `frontend/lib/api.ts` and as the Dockerfile `ARG` default — a prod image
  built without overriding this `ARG` silently ships pointing at localhost
  (baked into the client bundle; cannot be fixed post-build without rebuilding).
  Confirmed present: `frontend/.env.local` in this checkout points at
  `http://localhost:8124` (a local dev override) — this file is a session
  artifact of local dev, not something the deployed build reads.
- `SML_DATABASE_URL` default is a relative sqlite path (`./data/...`) — fine
  for local dev, wrong for any container without an explicit persistent
  volume mounted at that relative path, since a container filesystem is
  ephemeral without one.
- `SML_CHROMA_PERSIST_DIR` default (`./data/chroma`) has the same relative
  ephemeral-container issue.

## 5. Hardcoded ports

- API: `$PORT` env var (Railway/Render-style), read directly in `Procfile`'s
  `uvicorn` command — not hardcoded. Good.
- Frontend: `EXPOSE 3000` / `ENV PORT=3000` in `frontend/Dockerfile` — fixed
  at 3000 inside the container, which is fine as long as the hosting platform
  maps it, not fine if a platform expects the container to read `$PORT`
  itself (Next.js standalone server does honor `$PORT` at runtime — the
  Dockerfile's `ENV PORT=3000` is just the default, and can be overridden).
- Redis default port 6379, Postgres default 5432 — both only appear inside
  default connection-string values, not hardcoded elsewhere.

## 6. Secrets / API keys found

- **`.env` in this checkout contains a live Groq API key**
  (`SML_LLM_API_KEY=gsk_...`) plus `SML_LLM_API_BASE`/`SML_LLM_MODEL`.
  `.env` is listed in `.gitignore` and this directory is confirmed **not**
  a git repository at all (`git status` fails with "not a git repository") —
  so nothing has been committed, but the key exists in plaintext on disk.
  **Action for the user**: rotate this key before/if this repo is ever
  initialized as git and pushed anywhere, and before sharing this machine's
  filesystem. Never printed in full above or elsewhere in this audit.
- `.env.example` correctly contains only variable names and safe defaults —
  no real secret present there. Good, no change needed.
- No API keys, passwords, or tokens found hardcoded in `app/`, `replay/`, or
  `frontend/` source.

## 7. Startup dependencies (what must be true for `api` to boot)

From `app/main.py`'s `lifespan`:

1. **Hard dependency** — Postgres/sqlite must be reachable; `make_engine()` +
   `init_models()` run unconditionally and unguarded. A DB failure here
   crashes startup (no try/except around it).
2. **Hard dependency** — the Chroma persist directory must be creatable/
   writable; `chroma_dir.mkdir(parents=True, exist_ok=True)` and
   `chromadb.PersistentClient(...)` are also unguarded.
3. **Soft dependency** — Redis + the ML model artifact, via
   `start_detection_consumer`: wrapped in `try/except Exception`, logs
   `detection_consumer_start_failed` and continues with
   `app.state.detection_consumer = None` on failure. This is a deliberate,
   documented degrade-gracefully path (memory-layer routes keep working with
   no detection pipeline running).
4. **Boot-time reindex** — every tenant's Chroma corpus is re-embedded and
   re-fit on every single boot (documented fix for the TF-IDF-vocabulary/
   Chroma-persistence mismatch bug). This means startup time scales with
   total memory-record count across all tenants; there is no upper bound or
   timeout on this loop today.

## 8. Persistent-data requirements

| Store | Must persist across restarts? | Current guarantee |
|---|---|---|
| Postgres/sqlite | Yes — source of truth | Yes, if `SML_DATABASE_URL` points at a real hosted Postgres with its own persistent storage. The sqlite default does **not** persist in most container platforms (ephemeral filesystem) unless a volume is mounted at the exact relative path. |
| Chroma | No (rebuildable cache) — but boot-time reindex makes this a performance concern, not a correctness one | Persists to `SML_CHROMA_PERSIST_DIR` if it's a mounted volume; safe to lose since it's rebuilt from Postgres at every boot |
| Redis Streams data | Consumer-group offsets/PEL must survive an `api` restart for at-least-once delivery to hold | Depends entirely on the hosted Redis provider's own persistence (AOF/RDB) — not configured or verified in this codebase |
| ML model artifact | Yes, read-only | Must ship inside whatever is deployed (image layer, or download step); no remote-fetch mechanism exists |

## 9. Network / health-check requirements

- `GET /health` exists (`app/main.py`) and returns `{"status": "ok"}`
  unconditionally — it does **not** check DB/Redis/model, so it cannot be
  used as a real readiness probe today. No `/ready` or `/live` endpoints
  exist yet (Phase 6 gap).
- No liveness/readiness distinction in `frontend/Dockerfile` either — no
  `HEALTHCHECK` instruction present.
- CORS is currently `allow_origins=["*"]`, `allow_methods=["*"]`,
  `allow_headers=["*"]` — explicitly documented in `app/main.py` as
  intentional for a single-tenant demo with no auth boundary yet, not an
  oversight. Still a blocker for "publicly deployable production-quality" —
  see Security section.

## 10. Security risks found

1. **No authentication/authorization anywhere.** `get_tenant_id()`
   (`app/memory/routes.py`) reads `X-Tenant-Id` directly from a request
   header with zero verification — any caller can read/write any tenant's
   data. This is documented in the code itself as a known, deliberate gap
   for a single-analyst demo, not something introduced by this audit.
2. **CORS wide open** (`allow_origins=["*"]`) — consistent with gap #1 above,
   but means literally any origin can call the API from a browser once it's
   publicly reachable.
3. **LLM prompt/data separation** — already correctly implemented:
   `app/chat/query.py`'s system prompt instructs the model to treat retrieved
   incident content as data, cites exact incident IDs, and — most
   importantly — the code path itself, not just prompt wording, refuses to
   call the LLM at all when retrieval finds nothing relevant
   (`SIMILARITY_THRESHOLD` gate). This is a real code-level guarantee, not
   just an instruction the model could ignore. No change needed here; must
   not be weakened during hardening.
4. **No request size/payload limits** configured anywhere (no
   `Content-Length` cap, no FastAPI body-size middleware) — a large POST to
   `/api/chat` or the memory ingest endpoint is unbounded today.
5. **No rate limiting** on any route, including the LLM-calling chat/report
   routes — a public deployment could run up LLM API costs with no cap.
6. **Debug/reload flags** — `Procfile`'s `uvicorn` command has no
   `--reload`/debug flag (good), but nothing explicitly sets
   `--log-level`/disables auto-docs (`/docs`, `/redoc` are open by default in
   FastAPI unless explicitly disabled — currently exposed).
7. **Live secret on disk** — see Section 6.
8. **No `.git` repository exists yet** in this checkout — not itself a
   security risk, but blocks every git-based deploy flow (Railway, Render,
   Vercel, most container registries' build-from-source paths) until
   initialized. Flagging here since it affects how secrets must be handled
   once git init happens (must gitignore `.env` from the very first commit,
   already correctly configured in `.gitignore`).

## 11. Deployment blockers (must fix before "publicly deployable")

- No git repository — most target platforms (Phase 12) deploy from git.
- No backend Dockerfile (only `frontend/Dockerfile` exists).
- No `docker-compose.prod.yml` or any compose file.
- No CI configuration (no `.github/` directory, no other CI config found).
- `/health` is not a real readiness check; no `/ready`/`/live` split.
- No CORS restriction mechanism (`CORS_ORIGINS` env var doesn't exist yet).
- No structured logging — only Python's stdlib `logging` module used ad hoc
  (`_log.exception(...)`, `_log.info(...)`) with no request ID, tenant ID,
  or latency fields attached.
- Relative-path defaults for sqlite DB and Chroma dir assume a persistent
  local filesystem, which most container platforms do not provide by
  default.

## 12. Free-tier limitations to plan around

- ChromaDB embedded + boot-time full reindex means cold-start time grows
  with total memory-record count — free-tier compute is usually slow/
  shared, so this could make cold starts (post-sleep, post-deploy) noticeably
  slow as demo data accumulates. No current cap exists on tenant/record count.
- Free-tier Postgres/Redis providers (Neon, Upstash, Railway's free Redis,
  etc.) commonly idle/sleep or cap connection counts — `make_engine()` uses
  SQLAlchemy's default async pool with no explicit pool-size tuning; worth
  setting explicit `pool_size`/`max_overflow`/`pool_pre_ping` for hosted
  free-tier Postgres, which is prone to closing idle connections without
  the client always be told this ties into Phase 3 recommendation.
- Free-tier compute is typically CPU-only, single small instance —
  consistent with the existing CPU-only Isolation Forest inference (no GPU
  dependency exists anywhere in this codebase today, so no change needed
  there).

## 13. Production recommendations (non-binding, for Phase 1+ to act on)

1. `git init` this repo before any hosted-platform deploy step, with `.env`
   already gitignored (already true) — verify no secret is staged before
   the first commit.
2. Add `CORS_ORIGINS`, `ENVIRONMENT`, `LOG_LEVEL` settings to
   `app/config.py` without removing the existing documented CORS rationale —
   default to permissive in `local`/`dev`, restrictive in `production`.
3. Split `/health` into `/live` (process-alive only, no dependency checks)
   and `/ready` (checks DB + Redis + model-loaded), per Phase 6.
4. Add a backend `Dockerfile` mirroring the existing `frontend/Dockerfile`'s
   care (non-root user, no dev flags, deterministic build).
5. Add explicit SQLAlchemy pool settings (`pool_pre_ping=True` at minimum)
   for hosted Postgres reliability.
6. Do not touch the LLM non-fabrication gate, the Isolation Forest artifact,
   or the Redis Streams PEL-based redelivery — all three are real,
   already-correct, already-tested behavior per this audit.

---

*This document is Phase 0 output only. No code was modified to produce it.*
