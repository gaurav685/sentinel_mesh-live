"""Exercises the Chroma+Postgres bridge end to end. Uses sqlite+aiosqlite
(in-memory) for the Postgres side and a real embedded Chroma client backed
by a temp dir — Chroma has no mockable seam worth mocking, it's already
in-process, so this runs it for real.
"""

from __future__ import annotations

import uuid

import chromadb
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.session import init_models, make_session_factory, session_scope
from app.memory.embedder import TfidfEmbedder
from app.memory.store import MemoryStore

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


@pytest.fixture
async def store_and_sessions(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://")  # fresh in-memory DB per test
    await init_models(engine)
    session_factory = make_session_factory(engine)

    chroma_client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    embedder = TfidfEmbedder(refit_every_n=50, refit_interval_seconds=9_999.0)
    store = MemoryStore(chroma_client, embedder)

    yield store, session_factory
    await engine.dispose()


async def test_add_persists_to_postgres_and_is_vector_searchable(store_and_sessions):
    store, session_factory = store_and_sessions

    async with session_scope(session_factory) as session:
        row = await store.add(
            session,
            tenant_id=TENANT_A,
            content="repeated authentication failures for alice from a single ip",
            memory_type="episodic",
            importance=0.7,
        )
        assert row.id is not None
        assert row.retrieval_count == 0

    async with session_scope(session_factory) as session:
        fetched = await store.get(session, row.id)
        assert fetched is not None
        assert fetched.content == "repeated authentication failures for alice from a single ip"

    results = await store.vector_search("authentication failures alice", tenant_id=TENANT_A, top_k=5)
    assert len(results) == 1
    found_id, similarity, content = results[0]
    assert found_id == row.id
    assert similarity > 0.0
    assert "alice" in content


async def test_vector_search_is_tenant_scoped(store_and_sessions):
    store, session_factory = store_and_sessions

    async with session_scope(session_factory) as session:
        await store.add(session, tenant_id=TENANT_A, content="ransomware encryption behavior on host-01")
        await store.add(session, tenant_id=TENANT_B, content="ransomware encryption behavior on host-02")

    results_a = await store.vector_search("ransomware encryption", tenant_id=TENANT_A, top_k=5)
    results_b = await store.vector_search("ransomware encryption", tenant_id=TENANT_B, top_k=5)

    assert len(results_a) == 1
    assert len(results_b) == 1
    assert "host-01" in results_a[0][2]
    assert "host-02" in results_b[0][2]


async def test_archived_memories_excluded_by_default(store_and_sessions):
    store, session_factory = store_and_sessions

    async with session_scope(session_factory) as session:
        row = await store.add(session, tenant_id=TENANT_A, content="isolation forest anomaly on host-42")

    async with session_scope(session_factory) as session:
        await store.set_status(session, row.id, archived=True)

    active_results = await store.vector_search("isolation forest anomaly", tenant_id=TENANT_A, top_k=5)
    all_results = await store.vector_search(
        "isolation forest anomaly", tenant_id=TENANT_A, top_k=5, include_inactive=True
    )

    assert active_results == []
    assert len(all_results) == 1


async def test_mark_retrieved_bumps_count_and_timestamp(store_and_sessions):
    store, session_factory = store_and_sessions

    async with session_scope(session_factory) as session:
        row = await store.add(session, tenant_id=TENANT_A, content="brute force scenario replayed for the demo")

    async with session_scope(session_factory) as session:
        await store.mark_retrieved(session, row.id)

    async with session_scope(session_factory) as session:
        fetched = await store.get(session, row.id)
        assert fetched.retrieval_count == 1
        assert fetched.last_retrieved_at is not None


async def test_refit_reindexes_whole_tenant_corpus(store_and_sessions):
    store, session_factory = store_and_sessions
    store._embedder.refit_every_n = 3  # force a refit partway through this test

    ids = []
    async with session_scope(session_factory) as session:
        for i in range(5):
            row = await store.add(
                session, tenant_id=TENANT_A, content=f"correlation engine built attack chain number {i}"
            )
            ids.append(row.id)

    # Every one of the 5 memories must still be findable after the refit
    # that happened mid-way through — proving the reindex covered the whole
    # corpus, not just the memories added after the refit triggered.
    results = await store.vector_search("attack chain", tenant_id=TENANT_A, top_k=10)
    found_ids = {r[0] for r in results}
    assert found_ids == set(ids)


async def test_rebuild_if_empty_restores_index_from_postgres(store_and_sessions, tmp_path):
    store, session_factory = store_and_sessions

    async with session_scope(session_factory) as session:
        row = await store.add(session, tenant_id=TENANT_A, content="threat intel feed flagged a known-bad ip")

    # Simulate a free-tier redeploy wiping Chroma's disk: fresh client, same
    # persist dir wouldn't actually be empty, so point at a brand new dir to
    # simulate the reset directly.
    fresh_chroma = chromadb.PersistentClient(path=str(tmp_path / "chroma_reset"))
    rebuilt_store = MemoryStore(fresh_chroma, store._embedder)

    async with session_scope(session_factory) as session:
        reindexed_count = await rebuilt_store.rebuild_if_empty(session, TENANT_A)
    assert reindexed_count == 1

    async with session_scope(session_factory) as session:
        second_call_count = await rebuilt_store.rebuild_if_empty(session, TENANT_A)
    assert second_call_count == 0  # already populated -> no-op

    results = await rebuilt_store.vector_search("threat intel bad ip", tenant_id=TENANT_A, top_k=5)
    assert len(results) == 1
    assert results[0][0] == row.id


async def test_fresh_embedder_against_populated_chroma_returns_nothing_until_reindexed(
    store_and_sessions, tmp_path
):
    """Regression test for a real bug found live (app/main.py): Chroma
    persists its vectors to disk across process restarts; TfidfEmbedder's
    fitted vocabulary does not -- it lives only in process memory. A fresh
    process pointed at the *same*, already-populated Chroma directory
    still starts with an unfitted embedder, so vector_search()'s
    `is_fitted` guard silently returns [] for every query until something
    reindexes it -- even though Chroma has real data the whole time."""
    store, session_factory = store_and_sessions

    async with session_scope(session_factory) as session:
        row = await store.add(session, tenant_id=TENANT_A, content="ransomware encryption behavior on host-9")

    # Same persist dir (data survives), but a brand new client + a brand
    # new, never-fitted embedder -- simulates a process restart, not a
    # wiped disk. `store_and_sessions` (conftest.py) points its Chroma
    # client at `tmp_path / "chroma"` -- same path, reused deliberately.
    same_dir_chroma = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    fresh_embedder = TfidfEmbedder(refit_every_n=50, refit_interval_seconds=9_999.0)
    restarted_store = MemoryStore(same_dir_chroma, fresh_embedder)

    assert restarted_store._collection.count() == 1  # Chroma really does still have it
    assert fresh_embedder.is_fitted is False

    before_reindex = await restarted_store.vector_search("ransomware encryption host-9", tenant_id=TENANT_A)
    assert before_reindex == []  # the bug, reproduced

    async with session_scope(session_factory) as session:
        await restarted_store.reindex_tenant(session, TENANT_A)

    after_reindex = await restarted_store.vector_search("ransomware encryption host-9", tenant_id=TENANT_A)
    assert len(after_reindex) == 1
    assert after_reindex[0][0] == row.id
