"""Stub :class:`ModelEndpointRepository`.

Model endpoints (embedder URLs, LLM URLs, reranker URLs etc.) are
configured in Hydra YAML today — runtime can't add/swap them without a
restart. A DB-backed registry is a post-refactoring P1 feature so
operators can repoint endpoints from an admin UI.
"""

from __future__ import annotations

from core.ports.model_endpoint_repo import ModelEndpointRepository
from services.persistence._stubs import _StubRepositoryBase, stub_not_implemented


class PgModelEndpointRepository(_StubRepositoryBase, ModelEndpointRepository):
    """TODO: real impl once the ``model_endpoints`` table is added."""

    async def get(self, name: str, model_type: str) -> dict | None:
        raise stub_not_implemented("DB-backed model endpoints")

    async def list_all(self, model_type: str | None = None) -> list[dict]:
        raise stub_not_implemented("DB-backed model endpoints")

    async def upsert(self, name: str, model_type: str, config: dict) -> dict:
        raise stub_not_implemented("DB-backed model endpoints")

    async def delete(self, name: str, model_type: str) -> bool:
        raise stub_not_implemented("DB-backed model endpoints")


__all__ = ["PgModelEndpointRepository"]
