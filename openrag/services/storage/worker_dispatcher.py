from __future__ import annotations

import uuid
from typing import Any

from core.indexing.dispatcher import IndexingDispatcher

DEFAULT_TIMEOUT = 60.0


class WorkerDispatcher(IndexingDispatcher):
    """Dispatcher that routes new indexing jobs through ``IndexerPool``.

    Delete, metadata update, and copy still use the legacy ``Indexer`` actor
    until the remaining write operations are migrated to the worker pipeline.
    """

    def __init__(
        self,
        *,
        pool: Any,
        indexer: Any,
        task_state_manager: Any,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._pool = pool
        self._indexer = indexer
        self._tsm = task_state_manager
        self._timeout = timeout

    async def _call(self, future: Any, task_description: str) -> Any:
        from services.workers.ray_utils import call_ray_actor_with_timeout

        return await call_ray_actor_with_timeout(
            future=future,
            timeout=self._timeout,
            task_description=task_description,
        )

    async def dispatch_indexing(
        self,
        *,
        path: str,
        metadata: dict,
        partition: str,
        user: dict | None,
        workspace_ids: list[str] | None,
        replace: bool,
    ) -> str:
        task_id = uuid.uuid4().hex

        await self._call(
            self._tsm.set_state.remote(task_id, "QUEUED"),
            task_description=f"set_state({task_id})",
        )

        user_metadata = {key: value for key, value in metadata.items() if key not in {"file_id", "source"}}
        await self._call(
            self._tsm.set_details.remote(
                task_id,
                file_id=metadata.get("file_id"),
                partition=partition,
                metadata=user_metadata,
                user_id=user.get("id") if user else None,
            ),
            task_description=f"set_details({task_id})",
        )

        task = self._pool.process_file.remote(
            task_id=task_id,
            path=path,
            metadata=metadata,
            partition=partition,
            user=user,
            workspace_ids=workspace_ids,
            replace=replace,
        )

        await self._call(
            self._tsm.set_object_ref.remote(task_id, {"ref": task}),
            task_description=f"set_object_ref({task_id})",
        )
        return task_id

    async def delete_file(self, file_id: str, partition: str) -> None:
        await self._call(
            self._indexer.delete_file.remote(file_id, partition),
            task_description=f"delete_file({file_id})",
        )

    async def update_file_metadata(
        self,
        file_id: str,
        metadata: dict,
        partition: str,
        user: dict | None,
    ) -> None:
        await self._call(
            self._indexer.update_file_metadata.remote(file_id, metadata, partition, user=user),
            task_description=f"update_file_metadata({file_id})",
        )

    async def copy_file(
        self,
        file_id: str,
        metadata: dict,
        partition: str,
        user: dict | None,
    ) -> None:
        await self._call(
            self._indexer.copy_file.remote(file_id=file_id, metadata=metadata, partition=partition, user=user),
            task_description=f"copy_file({file_id})",
        )

    async def get_task_state(self, task_id: str) -> str | None:
        return await self._call(
            self._tsm.get_state.remote(task_id),
            task_description=f"get_state({task_id})",
        )

    async def get_task_error(self, task_id: str) -> str | None:
        return await self._call(
            self._tsm.get_error.remote(task_id),
            task_description=f"get_error({task_id})",
        )

    async def cancel_task(self, task_id: str) -> bool:
        import ray

        obj_ref = await self._call(
            self._tsm.get_object_ref.remote(task_id),
            task_description=f"get_object_ref({task_id})",
        )
        if obj_ref is None:
            return False

        ray.cancel(obj_ref["ref"], recursive=True)
        await self._call(
            self._tsm.set_state.remote(task_id, "CANCELLED"),
            task_description=f"set_state({task_id})",
        )
        return True


def from_ray_namespace(namespace: str = "openrag", timeout: float = DEFAULT_TIMEOUT) -> WorkerDispatcher:
    import ray
    from services.workers.indexer_pool import build_indexer_pool

    return WorkerDispatcher(
        pool=build_indexer_pool(namespace=namespace),
        indexer=ray.get_actor("Indexer", namespace=namespace),
        task_state_manager=ray.get_actor("TaskStateManager", namespace=namespace),
        timeout=timeout,
    )


__all__ = ["WorkerDispatcher", "from_ray_namespace"]
