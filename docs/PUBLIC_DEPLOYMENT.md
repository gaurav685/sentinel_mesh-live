# Public Deployment

## Public URLs

```
Frontend:  https://sentinel-mesh-live.vercel.app
API:       https://sentinel-mesh-live.onrender.com
Health:    https://sentinel-mesh-live.onrender.com/health
Readiness: https://sentinel-mesh-live.onrender.com/ready
```

## Architecture

```
Browser
   |
https://sentinel-mesh-live.vercel.app  (Vercel, Next.js)
   |  HTTPS, CORS-restricted to this exact origin
https://sentinel-mesh-live.onrender.com  (Render, Docker/FastAPI)
   |
   +--> Neon Postgres (TLS)       -- source of truth
   +--> Upstash Redis (TLS)       -- telemetry.raw stream, consumer group
   +--> ChromaDB, embedded        -- rebuildable cache, reindexed from
   |                                  Postgres at every boot
   +--> Groq (OpenAI-compatible)  -- grounded chat / chain reports

replay (run locally/on demand, never continuously) --> Upstash Redis
```

## Providers

| Component | Provider |
|---|---|
| Frontend | Vercel (Hobby) |
| API | Render (free Web Service, Docker) |
| PostgreSQL | Neon (free tier) |
| Redis | Upstash (free tier, TLS) |
| LLM | Groq |
| Graph | Not used — see `docs/DEPLOYMENT_AUDIT.md` |

## Environment variables

Render (`api`): `SML_ENVIRONMENT`, `SML_DATABASE_URL`, `SML_REDIS_URL`,
`SML_LLM_API_KEY`, `SML_LLM_API_BASE`, `SML_LLM_MODEL`, `SML_CORS_ORIGINS`.
Vercel (`frontend`): `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_DEMO_TENANT_ID`.
Never committed — set directly in each provider's dashboard.

## Deployment procedure (already performed once; for a fresh setup)

1. Push this repo to GitHub.
2. Neon: create a project, get the connection string, convert to
   `postgresql+asyncpg://...?ssl=require`.
3. Upstash: create a Redis database, get the `rediss://` connection string.
4. Render: New Web Service → connect the GitHub repo → Docker runtime,
   Dockerfile path `Dockerfile` → set the env vars above → deploy.
5. Vercel: Add New Project → import the same repo → **Root Directory:
   `frontend`** → set `NEXT_PUBLIC_API_BASE_URL` to the Render URL from
   step 4 → deploy.
6. Back on Render: set `SML_CORS_ORIGINS` to the Vercel URL from step 5,
   save (triggers a redeploy).

## Redeployment procedure

Both Render and Vercel redeploy automatically on every push to `main`.
Changing an environment variable on Render also triggers a redeploy of the
same image with the new value (verified live in this pass — the
`SML_CORS_ORIGINS` change did exactly this, and all existing data survived
it since it lives in Neon/Upstash, not the container).

## Logs

Render dashboard → the service → **Logs** tab (live tail + history).
Vercel dashboard → the project → **Deployments** → a deployment → **Logs**
(build logs) or **Functions** (runtime logs, if any — this frontend has no
API routes of its own, so this will mostly be empty).

## Replay (controlled, real NSL-KDD data)

Never run continuously against the public deployment. From a local machine:

```
python -m replay.main \
  --redis-url "rediss://default:<password>@amused-crow-287615.upstash.io:6379" \
  --file demo_replay.txt --limit 200 --tenant-id 00000000-0000-0000-0000-000000000001
```

The public API's already-running detection consumer picks these up within
seconds — confirmed live in this pass.

## Troubleshooting

- **`{"detail":"Not Found"}` on the bare API root URL** — expected; this API
  never defines a `/` route, only `/health`, `/live`, `/ready`, and
  `/api/...`. Not a failure.
- **First request after idle is slow** — Render free tier cold-start
  (spins down after ~15 min of inactivity). Wait and retry; `/health` is
  the cheapest way to warm it.
- **CORS errors in the browser console** — check `SML_CORS_ORIGINS` on
  Render matches the frontend's exact origin (scheme + host, no trailing
  slash).
- **Chat/report return 503** — `SML_LLM_API_KEY` missing/invalid on Render,
  or Groq itself is unreachable; check Render's logs for `llm_call_failed`.
- **`/ready` reports `"database": "error: ..."`** — Neon connection string
  wrong, expired, or the Neon project is paused/deleted.

## Limitations

- Render free tier: cold starts after ~15 min idle, no persistent disk
  (not needed — see architecture above), 750 free instance-hours/month.
- Vercel Hobby plan: non-commercial use only.
- Neon free tier: 0.5GB storage, 100 CU-hours/month.
- Upstash free tier: 256MB, 500K commands/month.
- No custom domain — provider-generated HTTPS URLs only.
- Single API instance — no horizontal scaling (see `docs/DEPLOYMENT.md`'s
  consumer-identity note).
