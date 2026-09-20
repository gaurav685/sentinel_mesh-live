# Multi-stage build -> a lean standalone Next.js image (next.config.ts:
# output: "standalone"). Not part of the README's 2-process free-tier
# topology (api + replay) -- see README's "Frontend Dockerfile" note for
# why a static/edge host, not a third always-on process, is the actual
# free-tier-compatible deploy target for this image.

FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Build-time API base URL: baked into the client bundle, since
# NEXT_PUBLIC_* vars are resolved at build, not run, time.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ARG NEXT_PUBLIC_DEMO_TENANT_ID=00000000-0000-0000-0000-000000000001
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_PUBLIC_DEMO_TENANT_ID=${NEXT_PUBLIC_DEMO_TENANT_ID}
RUN npm run build

FROM node:20-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
ENV PORT=3000
CMD ["node", "server.js"]
