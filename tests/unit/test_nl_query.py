from __future__ import annotations

import uuid

from app.db.session import session_scope
from app.memory.nl_query import answer_query, hybrid_retrieve

TENANT = uuid.uuid4()


async def test_hybrid_retrieve_empty_store_returns_empty_list(store_and_sessions):
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        ranked = await hybrid_retrieve(store, session, "anything", tenant_id=TENANT)
    assert ranked == []


async def test_hybrid_retrieve_ranks_best_keyword_match_first(store_and_sessions):
    store, session_factory = store_and_sessions

    async with session_scope(session_factory) as session:
        target = await store.add(
            session,
            tenant_id=TENANT,
            content="repeated authentication failures for alice from a single ip address",
            importance=0.8,
        )
        await store.add(
            session,
            tenant_id=TENANT,
            content="ransomware encryption behavior observed on a file share",
            importance=0.8,
        )

    async with session_scope(session_factory) as session:
        ranked = await hybrid_retrieve(store, session, "authentication failures alice", tenant_id=TENANT)

    assert len(ranked) == 2
    assert ranked[0].row.id == target.id
    assert ranked[0].composite >= ranked[1].composite


async def test_hybrid_retrieve_reinforces_returned_memories(store_and_sessions):
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        row = await store.add(session, tenant_id=TENANT, content="brute force scenario replayed for the demo")

    async with session_scope(session_factory) as session:
        ranked = await hybrid_retrieve(store, session, "brute force scenario", tenant_id=TENANT)
    assert ranked[0].row.retrieval_count == 1  # bumped by reinforcement

    async with session_scope(session_factory) as session:
        fetched = await store.get(session, row.id)
        assert fetched.retrieval_count == 1
        assert fetched.last_retrieved_at is not None


async def test_hybrid_retrieve_reinforce_false_does_not_mutate(store_and_sessions):
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        row = await store.add(session, tenant_id=TENANT, content="threat intel feed flagged a known-bad ip")

    async with session_scope(session_factory) as session:
        await hybrid_retrieve(store, session, "threat intel bad ip", tenant_id=TENANT, reinforce=False)

    async with session_scope(session_factory) as session:
        fetched = await store.get(session, row.id)
        assert fetched.retrieval_count == 0
        assert fetched.last_retrieved_at is None


def test_answer_query_with_no_matches():
    result = answer_query("what happened here", [])
    assert result["matches"] == []
    assert "No matching memory" in result["answer"]


async def test_answer_query_grounds_answer_in_stored_content(store_and_sessions):
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        await store.add(
            session, tenant_id=TENANT, content="isolation forest anomaly detected on host-42 outbound traffic"
        )

    async with session_scope(session_factory) as session:
        ranked = await hybrid_retrieve(store, session, "isolation forest anomaly host-42", tenant_id=TENANT)
        result = answer_query("isolation forest anomaly host-42", ranked)

    assert "isolation forest anomaly detected on host-42" in result["answer"]
    assert len(result["matches"]) == 1
    assert result["matches"][0]["composite_score"] > 0.0
