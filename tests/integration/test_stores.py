"""Phase 7F — cross-store full-cycle (Phase 7B placeholder).

The full create-partition / upsert-chunks / search / delete integration test
named in the Phase 7F plan needs both :class:`PostgresStore` and the Phase 7B
``MilvusVectorStore``. Phase 7B is not yet landed, so this test is marked
``xfail`` rather than silently skipped — once 7B lands the body fills in and
the marker comes off in the same diff.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.mark.xfail(
    reason="Phase 7B MilvusVectorStore not landed — see REFACTORING_DECISION_LOG.md",
    strict=True,
)
async def test_cross_store_full_cycle():
    # The shape the eventual test will take:
    #   1. ``postgres_store.partition_repo`` creates a partition row.
    #   2. ``vector_store.upsert`` inserts pre-embedded chunks tagged with
    #      that partition.
    #   3. ``vector_store.search`` round-trips the embedding and returns the
    #      ids/text.
    #   4. ``postgres_store.partition_repo.delete_partition`` cascades the
    #      catalog rows; ``vector_store.delete`` clears the Milvus side.
    #
    # Until 7B exists there is nothing to assert, so this raises and trips
    # the xfail marker.
    raise NotImplementedError("Phase 7B MilvusVectorStore not landed")
