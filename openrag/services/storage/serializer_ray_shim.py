"""Transitional ``FileSerializer`` adapter over the Ray DocSerializer actor.

``ConversionService`` (Phase 8E) talks to the clean
``core.indexing.serializer.FileSerializer`` ABC; this shim plugs the
still-existing ``DocSerializer`` Ray actor into that ABC for the
duration of the Phase-8 shim period. Phase 9 replaces it with a direct
serializer call and deletes this file.

It reuses the legacy ``components.indexer.utils.files.serialize_file``
helper (which already routes through ``call_ray_actor_with_timeout``,
preserving the timeout behaviour the tools router relied on). The Ray
imports are deferred so this module stays importable without Ray (unit
tests inject a fake serializer instead).
"""

from __future__ import annotations

from core.indexing.serializer import FileSerializer

# Task-id label passed to the serializer when not running inside a Ray
# task (the FastAPI process is the Ray driver). Mirrors the role of the
# legacy router's ``ray.get_runtime_context().get_task_id()`` value —
# the serializer only uses it for logging / state keys.
_FALLBACK_TASK_ID = "tools-extract"


class SerializerRayShim(FileSerializer):
    """Adapter exposing the DocSerializer actor as a ``FileSerializer``."""

    async def serialize(self, path: str, metadata: dict) -> str:
        import ray
        from components.indexer.utils.files import serialize_file

        task_id = ray.get_runtime_context().get_task_id() or _FALLBACK_TASK_ID
        doc = await serialize_file(task_id, path=path, metadata=metadata)
        return doc.page_content


def from_ray_namespace() -> SerializerRayShim:
    """Build the shim. Convenience for the composition root.

    The DocSerializer actor is resolved lazily inside ``serialize_file``
    (per call), so nothing Ray-related runs at construction time.
    """
    return SerializerRayShim()
