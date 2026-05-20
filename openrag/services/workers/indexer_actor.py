from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from core.models.document import Document
from services.workers.pipeline_builder import IndexingPipeline


class IndexerWorker:
    """Pure-Python core of the thin indexer actor.

    ``@ray.remote`` is not applied here so the class is directly
    instantiable in tests.  The production Ray actor wraps this class
    (or applies ``@ray.remote`` at startup).

    State transitions reported to *task_state_manager* are compatible
    with the existing queue-monitoring states:

    ``SERIALIZING`` — processing has started (parse + chunk + embed + store)
    ``COMPLETED``   — pipeline finished successfully
    ``FAILED``      — pipeline raised; set via ``set_failed_if_not_cancelled``

    Callers are responsible for setting ``QUEUED`` *before* dispatching
    the task, and for storing the object ref via ``set_object_ref``.
    """

    def __init__(self, pipeline: IndexingPipeline, task_state_manager: Any) -> None:
        self._pipeline = pipeline
        self._tsm = task_state_manager

    async def process_file(
        self,
        *,
        task_id: str,
        path: str,
        metadata: dict[str, Any],
        partition: str,
        user: dict[str, Any] | None = None,
        workspace_ids: list[str] | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Run one file through the indexing pipeline.

        Returns a plain dict ``{"stored_count": int, "stage": "stored"}``
        on success.  On failure, state is set to FAILED and the exception
        is re-raised so the Ray task is marked as errored.
        """
        await self._tsm.set_state.remote(task_id, "SERIALIZING")
        try:
            document = _load_document(path, metadata, partition)
            row: dict[str, Any] = {
                "document": document,
                "partition": partition,
                "filename": Path(path).name,
                "language": metadata.get("language", "en"),
                "replace": replace,
                "user": user,
            }
            await self._pipeline.run(row)
            await self._tsm.set_state.remote(task_id, "COMPLETED")
            return {"stored_count": row.get("stored_count", 0), "stage": row.get("stage", "")}
        except Exception:
            tb = traceback.format_exc()
            await self._tsm.set_failed_if_not_cancelled.remote(task_id, tb)
            raise


def _load_document(path: str, metadata: dict[str, Any], partition: str) -> Document:
    p = Path(path)
    return Document(
        filename=metadata.get("file_id") or p.name,
        raw_bytes=p.read_bytes(),
        content_type=Document.detect_content_type(p.name),
        partition=partition,
        metadata=metadata,
    )


__all__ = ["IndexerWorker"]
