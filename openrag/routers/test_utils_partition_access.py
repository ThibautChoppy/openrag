from __future__ import annotations

import sys
import types

import pytest
from fastapi import HTTPException

_STUBBED_MODULES = (
    "utils",
    "utils.dependencies",
    "utils.logger",
    "openai",
)


def _install_runtime_stubs() -> dict[str, types.ModuleType | None]:
    previous_modules = {name: sys.modules.get(name) for name in _STUBBED_MODULES}

    utils_stub = types.ModuleType("utils")
    utils_stub.__path__ = []
    sys.modules["utils"] = utils_stub

    dependencies_stub = types.ModuleType("utils.dependencies")
    dependencies_stub.get_task_state_manager = lambda: None
    sys.modules["utils.dependencies"] = dependencies_stub

    def _logger():
        logger = types.SimpleNamespace(
            debug=lambda *args, **kwargs: None,
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        )
        logger.bind = lambda *args, **kwargs: logger
        return logger

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.get_logger = _logger
    sys.modules["utils.logger"] = logger_stub

    openai_stub = types.ModuleType("openai")
    openai_stub.AsyncOpenAI = object
    openai_stub.APITimeoutError = TimeoutError
    openai_stub.APIConnectionError = ConnectionError
    openai_stub.APIError = RuntimeError
    sys.modules["openai"] = openai_stub

    return previous_modules


def _restore_runtime_stubs(previous_modules: dict[str, types.ModuleType | None]) -> None:
    for name, previous_module in previous_modules.items():
        if previous_module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous_module


_PREVIOUS_MODULES = _install_runtime_stubs()

from routers.utils import ensure_partition_role  # noqa: E402
from services.orchestrators.auth_service import AuthService  # noqa: E402

_restore_runtime_stubs(_PREVIOUS_MODULES)


class FakePartitionService:
    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.checked: list[str] = []

    async def partition_exists(self, partition: str) -> bool:
        self.checked.append(partition)
        return partition in self.existing


@pytest.mark.asyncio
async def test_ensure_partition_role_allows_unknown_partition_without_membership():
    partition_service = FakePartitionService(existing=set())

    result = await ensure_partition_role(
        partition="new-partition",
        user={"id": 1},
        user_partitions=[],
        required_role="editor",
        auth_service=AuthService,
        partition_service=partition_service,
    )

    assert result is True
    assert partition_service.checked == ["new-partition"]


@pytest.mark.asyncio
async def test_ensure_partition_role_forbids_existing_partition_without_membership():
    partition_service = FakePartitionService(existing={"existing"})

    with pytest.raises(HTTPException) as exc:
        await ensure_partition_role(
            partition="existing",
            user={"id": 1},
            user_partitions=[],
            required_role="viewer",
            auth_service=AuthService,
            partition_service=partition_service,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Access to partition 'existing' forbidden"


@pytest.mark.asyncio
async def test_ensure_partition_role_delegates_membership_role_check_to_auth_service():
    partition_service = FakePartitionService(existing={"p"})

    with pytest.raises(HTTPException) as exc:
        await ensure_partition_role(
            partition="p",
            user={"id": 1},
            user_partitions=[{"partition": "p", "role": "viewer"}],
            required_role="editor",
            auth_service=AuthService,
            partition_service=partition_service,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Editor role required for partition 'p'"
    assert partition_service.checked == []
