from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from core.models.chunk import Chunk
from core.vector_stores.vector_store import VectorStore
from services.workers.stages._common import run_with_optional_timeout, scrub_credentials, stage_timeout


async def store_stage(
    row: MutableMapping[str, Any],
    vector_store: VectorStore,
    *,
    timeout: float | None = None,
    per_chunk_timeout: float = 0.0,
) -> MutableMapping[str, Any]:
    """Upsert ``row["chunks"]`` into the partition collection."""

    try:
        chunks = row.get("chunks")
        if not _is_chunk_list(chunks):
            raise ValueError("store_stage row must contain a list[Chunk] under 'chunks'")

        collection = str(row.get("partition") or "default")
        effective_timeout = stage_timeout(timeout, len(chunks), per_item_timeout=per_chunk_timeout)
        row["stored_count"] = await run_with_optional_timeout(
            lambda: vector_store.upsert(chunks, collection=collection),
            effective_timeout,
        )
        row["stage"] = "stored"
        row.pop("error", None)
        return row
    except Exception as exc:
        row["stage"] = "store_failed"
        row["error"] = str(exc)
        raise
    finally:
        scrub_credentials(row)


def _is_chunk_list(value: Any) -> bool:
    """Return whether ``value`` is a concrete list of domain chunks."""
    return isinstance(value, list) and all(isinstance(chunk, Chunk) for chunk in value)
