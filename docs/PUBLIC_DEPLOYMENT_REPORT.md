# SentinelMesh Public Deployment Report

## Status

`DEPLOYED`

## Public URLs

Frontend: https://sentinel-mesh-live.vercel.app
API: https://sentinel-mesh-live.onrender.com
Health: https://sentinel-mesh-live.onrender.com/health
Readiness: https://sentinel-mesh-live.onrender.com/ready

## Infrastructure

| Component  | Provider | Status |
| ---------- | -------- | ------ |
| Frontend   | Vercel (Hobby) | Live |
| API        | Render (free Web Service, Docker) | Live |
| PostgreSQL | Neon (free tier) | Live |
| Redis      | Upstash (free tier, TLS) | Live |
| Chroma     | Embedded in `api`, rebuildable cache | Live (reindexes from Postgres at boot) |
| AuraDB     | — | Not used — confirmed not integrated in this codebase |
| Groq       | Existing key | Live |

## End-to-End Verification

| Test                   | Result | Evidence |
| ---------------------- | ------ | -------- |
| Frontend public access | PASS | `GET https://sentinel-mesh-live.vercel.app/dashboard` → 200, real SSR shell, `onrender.com` baked into the client bundle, no `localhost` in any chunk |
| API public access      | PASS | `GET https://sentinel-mesh-live.onrender.com/health` → 200 |
| Health                 | PASS | `{"status":"ok"}` |
| Readiness              | PASS | `{"database":"ok","redis":"ok","model_loaded":true,"model_version":"nsl-kdd-v1","model_framework_version":"scikit-learn==1.9.1","model_artifact_checksum":"58771b97e3fa1279ccb9102b2a3aa73cde15e818fdd9796894e468c9a5c894c3"}` |
| PostgreSQL             | PASS | Real schema created on Neon; incidents/detections persisted and read back across multiple sessions/days |
| Redis                  | PASS | Real TLS `PING` succeeded; consumer group `detection-engine` joined and processed real stream entries on Upstash |
| ML model               | PASS | Real Isolation Forest artifact loads on Render; checksum matches the local artifact exactly |
| Real NSL-KDD replay    | PASS | `python -m replay.main --redis-url rediss://...upstash.io:6379 --file demo_replay.txt` — real rows, ground-truth labels withheld from the pipeline |
| Detection              | PASS | Real anomaly scores (`normalized_score=0.76`, `0.81`) via the public API |
| MITRE                  | PASS | Real technique mapping present on public `/api/v1/detections` responses |
| Chains                 | PASS | Real chain of 3 linked incidents spanning two different calendar days, confirming both correctness and cross-session persistence |
| Threat score           | PASS | Real computed composite scores present |
| Chat                   | PASS | `POST /api/chat` via public URL, real Groq call, real grounded answer |
| Citations              | PASS | Chat response's `matches` cite real, verifiable `memory_id`s matching stored incidents |
| Chain report           | PASS | `POST /api/v1/chains/{id}/report` via public URL — real chronology, real incident ids, disclaimer present |
| API restart/redeploy   | PASS | Changing `SML_CORS_ORIGINS` on Render triggered a real redeploy; all prior data (4 events, 1 chain) confirmed intact afterward |
| Redis restart          | Not independently tested this pass | Already verified in the local Docker pass (`docs/DEPLOYMENT_AUDIT.md`/prior session); Upstash's own infrastructure resilience wasn't separately re-tested here |
| Frontend restart       | Not applicable | Vercel serverless — no persistent frontend process to restart |
| Persistent data        | PASS | Same chain/incident data visible across replay runs performed on different days |

## Known Limitations

- Render free tier cold-starts after ~15 minutes idle — first request after
  that is slow. Not precisely timed in this pass.
- No custom domain — provider-generated HTTPS URLs (`*.vercel.app`,
  `*.onrender.com`).
- Single Render instance — no horizontal scaling (this app's Redis Streams
  consumer identity is per-process-unique, which is correct for one
  instance but still has no `XCLAIM` reaper for a dead consumer's PEL —
  documented, not a regression from this pass).
- No graph store (AuraDB) — confirmed never actually integrated into this
  codebase; not a gap introduced by this deployment.
- Free-tier resource caps apply: Neon 0.5GB/100 CU-hours, Upstash
  256MB/500K commands, Vercel Hobby (non-commercial only).
- The Groq API key was shared in this chat session and is visible in this
  conversation's transcript — treat it as exposed; rotate it if that
  matters for the account it belongs to, independent of it also being set
  correctly (and only) in Render's environment variables, never in git.

## Security Status

- HTTPS on both public URLs (provider-terminated, automatic) — confirmed.
- `SML_CORS_ORIGINS` restricted to the exact Vercel origin — confirmed live
  (allowed origin gets the CORS header, an arbitrary origin does not).
- No secrets in the frontend bundle — confirmed (grepped every shipped JS
  chunk; only the public API base URL is present, no keys).
- No secrets in git — confirmed (`.env`, `.neon` gitignored from the first
  commit; `git status` clean before every push).
- PostgreSQL/Redis not directly exposed — the app connects out to Neon/
  Upstash over their own TLS endpoints; nothing in this deployment opens an
  inbound port to either.
- Debug/docs disabled in production — `SML_ENVIRONMENT=production` on
  Render, which the app already uses to disable `/docs`/`/redoc`/
  `/openapi.json` (built in the earlier hardening pass; not re-verified
  against the live URL in this specific pass, since doing so would require
  probing the production system's disabled surface).
- Structured, secret-free error responses — pre-existing from the hardening
  pass, not modified here.

## Files Changed (this pass)

New: `docs/PUBLIC_DEPLOYMENT_AUDIT.md`, `docs/PUBLIC_DEPLOYMENT_DECISION.md`,
`docs/PUBLIC_DEPLOYMENT.md`, `docs/PUBLIC_DEPLOYMENT_REPORT.md`
Edited: `README.md` (Live Demo section, corrected stale Deployment status),
`.env` (local reference only, gitignored — real Neon/Upstash connection
strings), `.gitignore` (added `.neon`, done automatically by the Neon CLI)
No application code was changed in this pass — this was infrastructure-only,
as instructed.

## Deployment Commands

```
# Neon (Postgres) — one-time setup
npm i -g neon@latest
neon login   # or: export NEON_API_KEY=<key>
neon link --project-id <your-project-id> --branch production -y
neon connection-string production   # get the connection string

# local verification against real hosted services
python -m replay.main --redis-url "rediss://default:<password>@<host>:6379" \
  --file demo_replay.txt --limit 200 --tenant-id <tenant-uuid>

# Render / Vercel: no CLI used this pass — deployed via each provider's
# GitHub-connected dashboard (push to `main` redeploys both automatically)
git push origin main
```
