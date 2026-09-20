from __future__ import annotations

import uuid

import numpy as np
from sqlalchemy import select

from app.db.memory_models import MemoryRecordRow
from app.db.session import session_scope
from app.memory.consolidation import cluster_memories, deduplicate, extract_patterns

TENANT = uuid.uuid4()


def test_cluster_memories_falls_back_to_single_cluster_with_too_little_data():
    embeddings = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]])
    labels = cluster_memories(embeddings, min_k=2, max_k=8)
    assert (labels == 0).all()


def test_cluster_memories_finds_real_separated_groups():
    rng = np.random.default_rng(42)
    group_a = rng.normal(loc=[0.0, 0.0], scale=0.05, size=(8, 2))
    group_b = rng.normal(loc=[5.0, 5.0], scale=0.05, size=(8, 2))
    embeddings = np.vstack([group_a, group_b])

    labels = cluster_memories(embeddings, min_k=2, max_k=4)

    assert len(set(labels)) == 2
    assert len(set(labels[:8])) == 1  # group_a all one label
    assert len(set(labels[8:])) == 1  # group_b all one label
    assert labels[0] != labels[8]  # the two groups got different labels


async def test_deduplicate_merges_near_identical_memories(store_and_sessions):
    store, session_factory = store_and_sessions

    async with session_scope(session_factory) as session:
        keeper_target = await store.add(
            session, tenant_id=TENANT, content="repeated authentication failures for alice", importance=0.8
        )
        dup = await store.add(
            session, tenant_id=TENANT, content="repeated authentication failures for alice", importance=0.3
        )
        distinct = await store.add(
            session, tenant_id=TENANT, content="ransomware encryption behavior on host-77", importance=0.5
        )

    async with session_scope(session_factory) as session:
        result = await deduplicate(store, session, TENANT, cosine_threshold=0.9)

    assert result == {"processed": 3, "merged": 1}

    async with session_scope(session_factory) as session:
        keeper_row = await store.get(session, keeper_target.id)
        dup_row = await store.get(session, dup.id)
        distinct_row = await store.get(session, distinct.id)

        assert keeper_row.is_archived is False
        assert keeper_row.retrieval_count == 0  # dup had 0 retrievals to credit
        assert dup_row.is_archived is True
        assert distinct_row.is_archived is False

    # Archived duplicate must be excluded from active vector search.
    active = await store.vector_search("authentication failures alice", tenant_id=TENANT, top_k=10)
    active_ids = {r[0] for r in active}
    assert keeper_target.id in active_ids
    assert dup.id not in active_ids


async def test_deduplicate_below_threshold_does_not_merge(store_and_sessions):
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        await store.add(session, tenant_id=TENANT, content="port scan originating from 10.0.0.5")
        await store.add(session, tenant_id=TENANT, content="completely unrelated ransomware encryption event")

    async with session_scope(session_factory) as session:
        result = await deduplicate(store, session, TENANT, cosine_threshold=0.94)

    assert result["merged"] == 0


async def test_extract_patterns_groups_episodic_memories_by_topic(store_and_sessions):
    store, session_factory = store_and_sessions

    auth_incidents = [
        "authentication failure burst detected for host alpha during the night shift",
        "authentication failure burst detected for host beta during the night shift",
        "authentication failure burst detected for host gamma during the night shift",
        "authentication failure burst detected for host delta during the night shift",
    ]
    ransomware_incidents = [
        "ransomware encryption behavior observed on file share number one",
        "ransomware encryption behavior observed on file share number two",
        "ransomware encryption behavior observed on file share number three",
        "ransomware encryption behavior observed on file share number four",
    ]

    async with session_scope(session_factory) as session:
        for content in auth_incidents + ransomware_incidents:
            await store.add(session, tenant_id=TENANT, content=content, memory_type="episodic")

    async with session_scope(session_factory) as session:
        patterns = await extract_patterns(store, session, TENANT, min_cluster_size=3)

    assert len(patterns) >= 1
    total_members = sum(p["member_count"] for p in patterns)
    assert total_members >= 3
    for pattern in patterns:
        assert pattern["concept"]
        assert 0.0 < pattern["confidence"] <= 1.0
        assert len(pattern["member_ids"]) == pattern["member_count"]


async def test_extract_patterns_returns_empty_below_min_cluster_size(store_and_sessions):
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        await store.add(session, tenant_id=TENANT, content="only one episodic memory here")

    async with session_scope(session_factory) as session:
        patterns = await extract_patterns(store, session, TENANT, min_cluster_size=3)

    assert patterns == []
