"""Transitional port for the document-serialization operation.

``ConversionService`` (Phase 8E) exposes the ``extractText`` tool —
serialize an uploaded file to raw text. The work runs in the
``DocSerializer`` Ray actor; defining it on a dedicated port keeps the
orchestrator Ray-free (8H: no Ray import / remote call under
``services/orchestrators/``). A small shim in ``services/storage/``
adapts the actor to this interface during the shim period; Phase 9
swaps it for a direct serializer call and deletes the shim.

No Ray / LangChain types leak across this boundary — the serialized
document is returned as its plain text content.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class FileSerializer(ABC):
    """The single serialize operation the conversion orchestrator needs."""

    @abstractmethod
    async def serialize(self, path: str, metadata: dict) -> str:
        """Serialize the file at ``path`` and return its raw text content."""
        ...
