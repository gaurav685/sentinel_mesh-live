from __future__ import annotations

import chromadb
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.session import init_models, make_session_factory
from app.memory.embedder import TfidfEmbedder
from app.memory.store import MemoryStore


@pytest.fixture
async def store_and_sessions(tmp_path):
    """Shared across memory-layer tests: a real embedded Chroma client
    (temp dir) + a fresh in-memory sqlite DB per test."""
    engine = create_async_engine("sqlite+aiosqlite://")
    await init_models(engine)
    session_factory = make_session_factory(engine)

    chroma_client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    embedder = TfidfEmbedder(refit_every_n=50, refit_interval_seconds=9_999.0)
    store = MemoryStore(chroma_client, embedder)

    yield store, session_factory
    await engine.dispose()
