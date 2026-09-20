# Deployment Contract — SentinelMesh Live

Phase 1 output. Defines what each deployable is responsible for, so later
phases harden against a fixed contract instead of an implicit one. Reflects
the architecture confirmed in `docs/DEPLOYMENT_AUDIT.md` — no new services
introduced.

## Services

### API

FastAPI application. Entry point: `app.main:app`. Start command:
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

Responsibilities:

- REST API surface: memory routes (`/api/v1/memory/*`), detection routes
  (`/api/v1/detections`, `/api/v1/stats`, `/api/v1/chains*`), chat routes
  (`/api/chat`).
- Detection consumer: background asyncio task consuming `telemetry.raw` from
  Redis Streams, scoring with the Isolation Forest artifact, persisting
  detections, creating/linking incident memories.
- Consolidation scheduler: APScheduler job, interval-driven
  (`SML_CONSOLIDATION_INTERVAL_MINUTES`), runs dedup + pattern extraction for
  every tenant found in Postgres.
- Threat scoring, MITRE mapping, attack chain grouping (`app/detection/
  scoring.py`, `app/mitre/catalog.py`, `app/detection/chains.py`) — all
  computed synchronously on read, not as separate background jobs.
- LLM chat (`app/chat/query.py`) and chain report generation
  (`app/chat/report.py`) — both grounded, both gated by the non-fabrication
  rule (never call the LLM with no retrieved context).
- Owns all three data stores: Postgres/sqlite, embedded Chroma, and is the
  only process that talks to Redis as a *consumer* (replay is the only
  *producer*).

Must NOT do: run replay automatically. `api` never imports or invokes
`replay/main.py`; the two are decoupled by design (Phase 11's "do not run
replay automatically in production unless explicitly configured" rule holds
today with zero code changes needed — nothing in `app/` references `replay/`).

### REPLAY

Independent process. Entry point: `replay.main`. Start command:
`python -m replay.main [--file PATH] [--limit N] [--tenant-id UUID]`.

Responsibilities:

- Read NSL-KDD data from a local file.
- Replay telemetry onto Redis stream `telemetry.raw` at (per existing
  behavior) real wall-clock timestamps, placeholder IPs (the dataset has
  none).
- **Never** sends the dataset's ground-truth attack label into the pipeline
  — confirmed in `replay/main.py`: the label is read only to print a local
  summary line, not included in any published event field.

Must be independently startable/stoppable — confirmed true today: `replay`
has no dependency on `api`'s process lifetime, only on Redis being reachable
at `SML_REDIS_URL`. Running `replay` with `api` down queues events in the
stream (once a consumer group exists) or is dropped if no group exists yet;
running `api` with `replay` never started leaves the detection consumer
idle with nothing to consume. Neither crashes the other.

### FRONTEND

Next.js application. Build: `npm run build` (produces `.next/standalone`
per `output: "standalone"`). Start: `node server.js` (standalone) or
platform-native Next.js start.

Responsibilities:

- Dashboard (stat tiles, live alert feed, incident relationship graph) —
  `frontend/app/dashboard/page.tsx`.
- Incident detail page — `frontend/app/incident/`.
- Relationship graph — `frontend/components/IncidentGraph.tsx`, client-only
  (`dynamic(..., { ssr: false })`), rendered inside a `SectionErrorBoundary`.
- Chat with citations — `frontend/app/chat/`, `frontend/components/
  ChatAnswer.tsx`.
- Chain reports — `frontend/app/chains/`.

Talks to `api` only over HTTP (`frontend/lib/api.ts`, `API_BASE` from
`NEXT_PUBLIC_API_BASE_URL`, baked in at build time). No direct DB/Redis/
Chroma access from the frontend — confirmed, `frontend/` has no dependency
on any of those clients.

## Process topology (unchanged from existing architecture)

```
replay  --(XADD telemetry.raw)-->  Redis  --(XREADGROUP)-->  api (detection consumer)
                                                                 |
                                                                 v
                                                    Postgres + Chroma (owned by api)
                                                                 |
                                                                 v
                                                        frontend (HTTP only)
```

Three deployables total: `api`, `replay`, `frontend`. `replay` is opt-in per
environment — the deployment contract explicitly forbids wiring it to run
automatically on `api` startup or on every deploy; it is a manual/scheduled
action the operator (or a documented demo script) triggers deliberately.

## Contract boundaries this phase locks in (for Phase 2+ to build against)

- `api` must keep booting when Redis or the model artifact is unreachable
  (already true — `try/except` around `start_detection_consumer` in
  `app/main.py`); it must NOT be made to hard-fail on those, since the
  memory-layer/chat routes have no dependency on either.
- `api` must continue to hard-fail on Postgres/Chroma-directory failure at
  boot (already true, unguarded in `lifespan`) — these are non-optional per
  the existing architecture; Phase 2 should make the resulting failure
  message actionable, not change the fail/no-fail behavior itself.
- `replay` must never publish the dataset's ground-truth label onto the
  stream — this is a correctness/non-fabrication contract, not just a
  style choice, and no phase in this deployment effort may relax it.
- `frontend` must treat every API field as possibly missing/malformed
  (already partially true via `SectionErrorBoundary`) — Phase 9 hardens
  this further but does not introduce it new.
