"""Stub :class:`PresetRepository`.

Pipeline presets — named bundles of chunker/embedder/retriever config —
are P0 on the post-refactoring roadmap. They are the mechanism that
will let each partition pick its own pipeline configuration without
operators touching YAML. No table exists today.
"""

from __future__ import annotations

from openrag.core.ports.preset_repo import PresetRepository
from openrag.services.persistence._stubs import _StubRepositoryBase, stub_not_implemented


class PgPresetRepository(_StubRepositoryBase, PresetRepository):
    """TODO: real impl once the ``presets`` table is added — see REFACTORING P0 plan."""

    async def get(self, name: str, preset_type: str) -> dict | None:
        raise stub_not_implemented("Per-partition pipeline presets")

    async def list_all(self, preset_type: str | None = None) -> list[dict]:
        raise stub_not_implemented("Per-partition pipeline presets")

    async def upsert(self, name: str, preset_type: str, config: dict) -> dict:
        raise stub_not_implemented("Per-partition pipeline presets")

    async def delete(self, name: str, preset_type: str) -> bool:
        raise stub_not_implemented("Per-partition pipeline presets")


__all__ = ["PgPresetRepository"]
