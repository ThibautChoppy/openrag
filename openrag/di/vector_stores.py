"""Factory for the :class:`VectorStore` adapter.

The real Milvus adapter (``MilvusVectorStore``) is a Phase 7B deliverable.
Until it lands, vector operations continue to route through the legacy
``MilvusDB`` Ray actor wrapped by
:class:`services.storage.milvus_ray_shim.MilvusRayShim`. This factory exists
now so the Phase 7E composition root has a stable entry point — the only
change required when 7B lands is to swap the body of
:func:`create_vector_store`.

Calling the factory in its current state is a deliberate failure: it raises
:class:`NotImplementedError` rather than instantiating the placeholder
``MilvusStore`` class (which is abstract and would surface a confusing
``TypeError`` instead).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config.root import Settings
    from core.vector_stores import VectorStore


def create_vector_store(settings: Settings) -> VectorStore:  # noqa: ARG001
    """Build the vector store adapter from the root settings.

    Currently raises — see the module docstring. Phase 7B fills in the body
    by returning ``MilvusVectorStore(settings.vectordb)``.
    """
    raise NotImplementedError(
        "create_vector_store() is a Phase 7B deliverable. Until "
        "MilvusVectorStore lands, vector operations route through the "
        "legacy Vectordb Ray actor (see services/storage/milvus_ray_shim.py).",
    )


__all__ = ["create_vector_store"]
