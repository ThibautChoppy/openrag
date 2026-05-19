"""Transitional ``IndexingDispatcher`` adapter over the Ray worker actors.

``IndexingService`` (Phase 8D.1) talks to the clean
``core.indexing.dispatcher.IndexingDispatcher`` ABC; this shim plugs the
still-existing ``Indexer`` + ``TaskStateManager`` Ray actors into that
ABC for the duration of the Phase-8 shim period. Phase 9 replaces it
with a direct pipeline call and deletes this file.

Bookkeeping calls (state / object-ref / cancel) are routed through
``call_ray_actor_with_timeout`` so timeout & cancellation behaviour
matches the rest of the legacy app. The indexing job itself is *not*
awaited — ``add_file`` is fire-and-forget on the actor; we only capture
its task id and register the object ref so it can be cancelled later
(legacy ``routers/indexer.py`` behaviour, preserved exactly).

The Ray imports are deferred to method bodies / the factory so this
module stays importable without Ray (unit tests inject a fake actor).
"""

from __future__ import annotations

from typing import Any

from core.indexing.dispatcher import IndexingDispatcher

# Mirrors the legacy default in routers/indexer.py
# (config.ray.indexer.vectordb_timeout). Override via the factory.
DEFAULT_TIMEOUT = 60.0


class IndexerRayShim(IndexingDispatcher):
    """Adapter exposing the Indexer + TaskStateManager actors as a dispatcher.

    Args:
        indexer: Ray actor handle for ``Indexer`` (or any object whose
            remote methods match it — handy for tests).
        task_state_manager: Ray actor handle for ``TaskStateManager``.
        timeout: Per-call timeout for the bookkeeping calls.
    """

    def __init__(self, indexer: Any, task_state_manager: Any, timeout: float = DEFAULT_TIMEOUT) -> None:
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
        # Fire-and-forget on the actor: we keep the ObjectRef so the task
        # can be cancelled, but never await it here (the work runs async).
        task = self._indexer.add_file.remote(
            path=path,
            metadata=metadata,
            partition=partition,
            user=user,
            workspace_ids=workspace_ids,
            replace=replace,
        )
        task_id = task.task_id().hex()
        await self._call(
            self._tsm.set_state.remote(task_id, "QUEUED"),
            task_description=f"set_state({task_id})",
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
        current_state = await self._call(
            self._tsm.get_state.remote(task_id),
            task_description=f"get_state({task_id})",
        )
        if current_state not in {"COMPLETED", "FAILED"}:
            await self._call(
                self._tsm.set_state.remote(task_id, "CANCELLED"),
                task_description=f"set_state({task_id})",
            )
        return True


def from_ray_namespace(
    namespace: str = "openrag",
    timeout: float = DEFAULT_TIMEOUT,
) -> IndexerRayShim:
    """Look up the Indexer + TaskStateManager actors and wrap them.

    Convenience for the composition root. The Ray import is deferred so
    importing this module without Ray (unit tests with a fake actor)
    does not fail.
    """
    import ray

    return IndexerRayShim(
        ray.get_actor("Indexer", namespace=namespace),
        ray.get_actor("TaskStateManager", namespace=namespace),
        timeout=timeout,
    )
