"""FastAPI dependency providers.

Thin accessors over the request-scoped :class:`ServiceContainer` that
``main.py`` attaches at ``app.state.container``. Phase 8 keeps these as
one-liners — the container (``di/container.py``) is the composition
root. Phase 11 moves the attachment into a proper FastAPI lifespan and
wires ``container.initialize()``; until then the OIDC flow that needs
the asyncpg pool is dormant (token-mode auth routes already short-circuit
before reaching a service).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, status

if TYPE_CHECKING:
    from di.container import ServiceContainer
    from services.orchestrators.auth_service import AuthService
    from services.orchestrators.partition_service import PartitionService
    from services.orchestrators.retrieval_service import RetrievalService
    from services.orchestrators.user_service import UserService
    from services.orchestrators.workspace_service import WorkspaceService


def get_container(request: Request) -> ServiceContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service container is not available.",
        )
    return container


def get_auth_service(request: Request) -> AuthService:
    return get_container(request).auth_service


def get_user_service(request: Request) -> UserService:
    return get_container(request).user_service


def get_partition_service(request: Request) -> PartitionService:
    return get_container(request).partition_service


def get_workspace_service(request: Request) -> WorkspaceService:
    return get_container(request).workspace_service


def get_retrieval_service(request: Request) -> RetrievalService:
    return get_container(request).retrieval_service
