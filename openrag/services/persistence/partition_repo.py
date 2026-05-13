"""Postgres implementation of :class:`PartitionRepository`.

Manages the ``partitions`` table — the global registry of document
collections. The legacy
:class:`components.indexer.vectordb.utils.PartitionFileManager` exposed
``create_partition``, ``delete_partition``, ``list_partitions``,
``partition_exists``, ``get_partition_file_count``, ``get_total_file_count``
here; all six map onto this class.

Deleting a partition cascades to ``files``, ``partition_memberships``,
and ``workspaces`` via the FK ``ON DELETE CASCADE`` rules in the schema.
Per-uploader ``file_count`` is decremented in application code (no SQL
trigger) so the books stay balanced.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from openrag.core.ports.partition_repo import PartitionRepository

if TYPE_CHECKING:
    import asyncpg


class PgPartitionRepository(PartitionRepository):
    """asyncpg-backed implementation of :class:`PartitionRepository`."""

    def __init__(self, pool_getter: Callable[[], asyncpg.Pool]) -> None:
        self._pool_getter = pool_getter

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool_getter()

    # ── PartitionRepository port methods ─────────────────────────────

    async def create_partition(self, name: str, user_id: int | None = None) -> dict:
        """Insert a partition row; idempotent on the unique constraint.

        When ``user_id`` is provided the caller is granted ``owner`` on
        creation. Existing partitions are returned unchanged with no
        membership churn — matches the legacy "already exists, log and
        skip" behaviour.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM partitions WHERE partition = $1",
                    name,
                )
                if row is None:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO partitions (partition, created_at)
                        VALUES ($1, NOW())
                        RETURNING *
                        """,
                        name,
                    )
                    if user_id is not None:
                        await conn.execute(
                            """
                            INSERT INTO partition_memberships
                                (partition_name, user_id, role, added_at)
                            VALUES ($1, $2, 'owner', NOW())
                            ON CONFLICT (partition_name, user_id) DO NOTHING
                            """,
                            name,
                            user_id,
                        )
        return self._row_to_dict(row)

    async def get_partition(self, name: str) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM partitions WHERE partition = $1",
            name,
        )
        return self._row_to_dict(row) if row else None

    async def list_partitions(self) -> list[dict]:
        rows = await self.pool.fetch("SELECT * FROM partitions ORDER BY created_at")
        return [self._row_to_dict(r) for r in rows]

    async def delete_partition(self, name: str) -> bool:
        """Delete a partition + cascade files/memberships/workspaces.

        Mirrors the legacy bookkeeping: before the cascade we count files
        per uploader and decrement each uploader's ``file_count`` by that
        amount (clamped at zero) so quotas stay accurate.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                exists = await conn.fetchval(
                    "SELECT 1 FROM partitions WHERE partition = $1",
                    name,
                )
                if not exists:
                    return False
                uploader_counts = await conn.fetch(
                    """
                    SELECT created_by, COUNT(*)::int AS n
                    FROM files
                    WHERE partition_name = $1 AND created_by IS NOT NULL
                    GROUP BY created_by
                    """,
                    name,
                )
                await conn.execute(
                    "DELETE FROM partitions WHERE partition = $1",
                    name,
                )
                for r in uploader_counts:
                    await conn.execute(
                        "UPDATE users SET file_count = GREATEST(file_count - $1, 0) WHERE id = $2",
                        r["n"],
                        r["created_by"],
                    )
                return True

    async def partition_exists(self, name: str) -> bool:
        return await self.pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM partitions WHERE partition = $1)",
            name,
        )

    # ── Legacy method names used by the Phase 7C shim ────────────────

    async def get_partition_file_count(self, partition: str) -> int:
        """TODO(phase-9): remove."""
        return await self.pool.fetchval(
            "SELECT COUNT(*)::int FROM files WHERE partition_name = $1",
            partition,
        )

    async def get_total_file_count(self) -> int:
        """TODO(phase-9): remove."""
        return await self.pool.fetchval("SELECT COUNT(*)::int FROM files")

    # ── Row → dict helper ────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: asyncpg.Record) -> dict:
        """Shape mirrors the legacy ``Partition.to_dict()`` ORM helper."""
        created = row["created_at"]
        return {
            "partition": row["partition"],
            "created_at": created.isoformat() if created else None,
        }


__all__ = ["PgPartitionRepository"]
