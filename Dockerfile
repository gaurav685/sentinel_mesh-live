# Backend `api` image -- mirrors frontend/Dockerfile's care (pinned base,
# non-root user, no dev flags, deterministic build). `replay` reuses this
# same image (see Dockerfile.replay) rather than duplicating dependency
# installation, since both are the same Python package with different
# entry points.

FROM python:3.11-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY replay ./replay
COPY ml ./ml
COPY conftest.py pyproject.toml ./

RUN addgroup --system --gid 1001 sentinelmesh && \
    adduser --system --uid 1001 --gid 1001 --no-create-home sentinelmesh
# Real bug, found running this under docker-compose with a named volume at
# this exact path: a volume mounted over a path that does NOT already
# exist in the image is created root-owned, regardless of the container's
# USER -- chromadb (running as `sentinelmesh` below) then fails with
# "unable to open database file". Pre-creating and chowning the *exact*
# mount path here, not just its parent, makes Docker copy this ownership
# into the volume on first mount instead.
RUN mkdir -p /app/data/chroma && chown -R sentinelmesh:sentinelmesh /app/data
USER sentinelmesh

# SML_DATABASE_URL/SML_CHROMA_PERSIST_DIR should point at a mounted volume
# in any real deployment (Phase 3) -- /app/data exists and is writable by
# the non-root user as a same-container fallback only.
ENV SML_CHROMA_PERSIST_DIR=/app/data/chroma

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/live', timeout=3)" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
