from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _remote_mock(return_value: Any = None) -> MagicMock:
    method = MagicMock()
    method.remote = AsyncMock(return_value=return_value)
    return method


def _pool_with_ref(ref: object) -> MagicMock:
    pool = MagicMock()
    pool.process_file = MagicMock()
    pool.process_file.remote = MagicMock(return_value=ref)
    return pool


def _legacy_indexer() -> MagicMock:
    indexer = MagicMock()
    indexer.delete_file = _remote_mock()
    indexer.update_file_metadata = _remote_mock()
    indexer.copy_file = _remote_mock()
    return indexer


def _task_state_manager() -> MagicMock:
    tsm = MagicMock()
    tsm.set_state = _remote_mock()
    tsm.set_details = _remote_mock()
    tsm.set_object_ref = _remote_mock()
    tsm.get_state = _remote_mock("SERIALIZING")
    tsm.get_error = _remote_mock("traceback")
    tsm.get_object_ref = _remote_mock({"ref": object()})
    return tsm


@pytest.mark.asyncio
async def test_dispatch_indexing_queues_worker_pool_task_and_records_ref() -> None:
    from services.storage.worker_dispatcher import WorkerDispatcher

    ref = object()
    pool = _pool_with_ref(ref)
    tsm = _task_state_manager()
    dispatcher = WorkerDispatcher(pool=pool, indexer=_legacy_indexer(), task_state_manager=tsm)

    with patch("services.storage.worker_dispatcher.uuid") as mock_uuid:
        mock_uuid.uuid4.return_value.hex = "task-1"
        task_id = await dispatcher.dispatch_indexing(
            path="/data/report.txt",
            metadata={"file_id": "file-1", "source": "/data/report.txt", "filename": "report.txt"},
            partition="tenant-a",
            user={"id": 42},
            workspace_ids=["ws-1"],
            replace=True,
        )

    assert task_id == "task-1"
    tsm.set_state.remote.assert_called_once_with("task-1", "QUEUED")
    tsm.set_details.remote.assert_called_once_with(
        "task-1",
        file_id="file-1",
        partition="tenant-a",
        metadata={"filename": "report.txt"},
        user_id=42,
    )
    pool.process_file.remote.assert_called_once_with(
        task_id="task-1",
        path="/data/report.txt",
        metadata={"file_id": "file-1", "source": "/data/report.txt", "filename": "report.txt"},
        partition="tenant-a",
        user={"id": 42},
        workspace_ids=["ws-1"],
        replace=True,
    )
    tsm.set_object_ref.remote.assert_called_once_with("task-1", {"ref": ref})


@pytest.mark.asyncio
async def test_worker_dispatcher_keeps_mutating_operations_on_legacy_indexer() -> None:
    from services.storage.worker_dispatcher import WorkerDispatcher

    indexer = _legacy_indexer()
    dispatcher = WorkerDispatcher(
        pool=_pool_with_ref(object()),
        indexer=indexer,
        task_state_manager=_task_state_manager(),
    )

    await dispatcher.delete_file("file-1", "tenant-a")
    await dispatcher.update_file_metadata("file-1", {"title": "new"}, "tenant-a", user={"id": 7})
    await dispatcher.copy_file("file-1", {"file_id": "copy-1"}, "tenant-b", user=None)

    indexer.delete_file.remote.assert_called_once_with("file-1", "tenant-a")
    indexer.update_file_metadata.remote.assert_called_once_with("file-1", {"title": "new"}, "tenant-a", user={"id": 7})
    indexer.copy_file.remote.assert_called_once_with(
        file_id="file-1",
        metadata={"file_id": "copy-1"},
        partition="tenant-b",
        user=None,
    )


@pytest.mark.asyncio
async def test_cancel_task_uses_stored_pool_object_ref() -> None:
    from services.storage.worker_dispatcher import WorkerDispatcher

    ref = object()
    tsm = _task_state_manager()
    tsm.get_object_ref.remote = AsyncMock(return_value={"ref": ref})
    tsm.get_state.remote = AsyncMock(return_value="SERIALIZING")
    dispatcher = WorkerDispatcher(pool=_pool_with_ref(object()), indexer=_legacy_indexer(), task_state_manager=tsm)

    with patch("ray.cancel") as cancel:
        result = await dispatcher.cancel_task("task-1")

    assert result is True
    cancel.assert_called_once_with(ref, recursive=True)
    tsm.set_state.remote.assert_called_once_with("task-1", "CANCELLED")


@pytest.mark.asyncio
async def test_cancel_task_marks_cancelled_even_if_worker_finished_first() -> None:
    from services.storage.worker_dispatcher import WorkerDispatcher

    ref = object()
    tsm = _task_state_manager()
    tsm.get_object_ref.remote = AsyncMock(return_value={"ref": ref})
    tsm.get_state.remote = AsyncMock(return_value="COMPLETED")
    dispatcher = WorkerDispatcher(pool=_pool_with_ref(object()), indexer=_legacy_indexer(), task_state_manager=tsm)

    with patch("ray.cancel"):
        result = await dispatcher.cancel_task("task-1")

    assert result is True
    tsm.set_state.remote.assert_called_once_with("task-1", "CANCELLED")
