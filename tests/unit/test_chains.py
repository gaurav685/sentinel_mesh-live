from __future__ import annotations

import uuid

from app.db.session import session_scope
from app.detection.chains import build_chains, get_chain_for_memory, parse_linked_ids

TENANT = uuid.uuid4()


def test_parse_linked_ids_extracts_uuids_only():
    fake_id = uuid.uuid4()
    tags = ["isolation_forest", f"linked_to:{fake_id}", "not_a_link_tag", "linked_to:not-a-uuid"]
    assert parse_linked_ids(tags) == [fake_id]


async def test_build_chains_groups_two_linked_incidents(store_and_sessions):
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        first = await store.add(session, tenant_id=TENANT, content="apache2 signature occurrence 1", memory_type="incident")
        second = await store.add(
            session, tenant_id=TENANT, content="apache2 signature occurrence 2",
            memory_type="incident", tags=[f"linked_to:{first.id}"],
        )
        await store.add(session, tenant_id=TENANT, content="unrelated standalone incident", memory_type="incident")

    async with session_scope(session_factory) as session:
        chains = await build_chains(session, TENANT)

    assert len(chains) == 1
    chain = chains[0]
    assert chain.length == 2
    assert [m.id for m in chain.memories] == [first.id, second.id]  # chronological


async def test_build_chains_excludes_standalone_incidents(store_and_sessions):
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        await store.add(session, tenant_id=TENANT, content="unrelated standalone incident on host-88", memory_type="incident")
        await store.add(session, tenant_id=TENANT, content="unrelated standalone incident on host-99", memory_type="incident")

    async with session_scope(session_factory) as session:
        chains = await build_chains(session, TENANT)
    assert chains == []


async def test_build_chains_includes_archived_side_of_a_link(store_and_sessions):
    """The exact real bug found in the frontend: the memory carrying the
    `linked_to` tag is often the one consolidation archives. A chain
    query must still surface it."""
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        keeper = await store.add(
            session, tenant_id=TENANT, content="brute force scenario on host-keeper", memory_type="incident"
        )
        merged = await store.add(
            session, tenant_id=TENANT, content="brute force scenario on host-merged", memory_type="incident",
            tags=[f"linked_to:{keeper.id}"],
        )
        await store.set_status(session, merged.id, archived=True)

    async with session_scope(session_factory) as session:
        chains = await build_chains(session, TENANT)

    assert len(chains) == 1
    assert {m.id for m in chains[0].memories} == {keeper.id, merged.id}


async def test_three_way_chain_groups_as_one_component(store_and_sessions):
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        a = await store.add(session, tenant_id=TENANT, content="ransomware behavior on host-alpha", memory_type="incident")
        b = await store.add(
            session, tenant_id=TENANT, content="ransomware behavior on host-beta", memory_type="incident", tags=[f"linked_to:{a.id}"]
        )
        c = await store.add(
            session, tenant_id=TENANT, content="ransomware behavior on host-gamma", memory_type="incident", tags=[f"linked_to:{b.id}"]
        )

    async with session_scope(session_factory) as session:
        chains = await build_chains(session, TENANT)

    assert len(chains) == 1
    assert {m.id for m in chains[0].memories} == {a.id, b.id, c.id}


async def test_get_chain_for_memory_finds_the_right_chain(store_and_sessions):
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        first = await store.add(session, tenant_id=TENANT, content="port scan activity on host-x", memory_type="incident")
        second = await store.add(
            session, tenant_id=TENANT, content="port scan activity on host-y", memory_type="incident", tags=[f"linked_to:{first.id}"]
        )

    async with session_scope(session_factory) as session:
        chain = await get_chain_for_memory(session, TENANT, second.id)
    assert chain is not None
    assert chain.length == 2


async def test_get_chain_for_memory_returns_none_for_standalone(store_and_sessions):
    store, session_factory = store_and_sessions
    async with session_scope(session_factory) as session:
        solo = await store.add(session, tenant_id=TENANT, content="standalone anomaly on host-solo", memory_type="incident")

    async with session_scope(session_factory) as session:
        chain = await get_chain_for_memory(session, TENANT, solo.id)
    assert chain is None
