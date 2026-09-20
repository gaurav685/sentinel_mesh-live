# Deployment — SentinelMesh Live

## Chosen deployment target: Docker Compose (self-hosted)

Per an explicit decision made during this hardening pass, this project's
deployment artifact is **`docker-compose.prod.yml`**, run on any machine or
VM with Docker installed -- not a signed-up-for hosted platform (Railway,
Render, Vercel, etc.). This section documents that choice honestly: no
hosted provider account was created or configured as part of this work, and
no hosted URLs exist. Everything below was live-verified locally against
real containers, real Postgres, real Redis, and a real OpenAI-compatible
LLM endpoint (Groq) during this hardening pass -- see
`docs/DEPLOYMENT_AUDIT.md` and the smoke-test evidence in this repo's
session history for exact commands and real output.

This keeps every requirement Phase 12 actually cares about (provider-
agnostic application code, no hardcoded infrastructure, reproducible
deployment) while avoiding unnecessary infrastructure and third-party
accounts the project doesn't need yet (Rule 13: "Do not over-engineer the
deployment").

If a hosted deployment is wanted later, `docker-compose.prod.yml`'s
`api`/`frontend` images build and run unmodified on any container-capable
host (Railway, Render, Fly.io, a bare VM); `SML_DATABASE_URL`/`SML_REDIS_URL`
simply point at a hosted Postgres/Redis instead of the compose-local
containers -- no application code changes, only environment variables.

## Running it

```
cp .env.example .env
# fill in SML_LLM_API_KEY and POSTGRES_PASSWORD in .env

docker compose -f docker-compose.prod.yml up -d --build
```

This starts, in order (health-check-gated): `postgres`, `redis`, then `api`
(waits for both to be healthy), then `frontend` (waits for `api` to be
healthy).

- Frontend: http://localhost:3000
- API: http://localhost:8000
- API health: http://localhost:8000/health · http://localhost:8000/live · http://localhost:8000/ready

Replay real NSL-KDD data (never starts automatically -- see
`docker-compose.prod.yml`'s `replay` service, profile `tools`):

```
docker compose -f docker-compose.prod.yml --profile tools run --rm replay \
  --file /data/host/demo_replay.txt --limit 200
```

(`/data/host` is the repo root, mounted read-only into the `replay`
container so it can read any NSL-KDD file already present in this repo.)

Stop everything, keep data:

```
docker compose -f docker-compose.prod.yml down
```

Stop everything, discard data:

```
docker compose -f docker-compose.prod.yml down -v
```

## Environment variables

See `.env.example` for the full list with descriptions. Required in
`docker-compose.prod.yml`: `POSTGRES_PASSWORD`, `SML_LLM_API_KEY`. Everything
else has a documented default.

## Limitations of this deployment target

- **No public URL.** This runs wherever Docker runs it (a laptop, a VM you
  control) -- it is not reachable from the internet unless you put it
  behind your own reverse proxy/TLS termination and open the relevant
  ports, which is outside this project's scope.
- **No horizontal scaling.** `docker-compose.prod.yml` runs exactly one
  `api` replica. `app/detection/consumer.py`'s `consumer_name` now defaults
  to a per-process value (hostname+pid) rather than a fixed literal --
  fixed during this hardening pass after live-reproducing the silent
  event-split it caused when two processes shared one identity -- but
  there is still no `XCLAIM`/`XAUTOCLAIM` reaper for a consumer that dies
  mid-batch (documented limitation in `app/bus/redis_streams.py`), so a
  real multi-replica deployment would still need that before scaling out.
- **No TLS.** Both services serve plain HTTP inside the compose network and
  on the host ports it publishes.
- **Sleep/cold-start behavior**: none -- containers run continuously once
  started, unlike a serverless/free-tier hosted platform's sleep-on-idle
  behavior. The tradeoff is that this machine must stay on for the
  deployment to stay up.
- **Storage**: bounded only by the host's disk (three named Docker
  volumes: `postgres_data`, `redis_data`, `chroma_data`).
