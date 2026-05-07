"""Service container — wires registries and exposes component factories."""

from __future__ import annotations

from core.embeddings import embedder_registry
from core.llm import llm_registry
from core.rerankers import reranker_registry
from core.vlm import vlm_registry
from di.embedders import register_embedders
from di.llms import register_llms
from di.rerankers import register_rerankers
from di.vlms import register_vlms


class ServiceContainer:
    """Populates registries and provides typed factory access."""

    def __init__(self) -> None:
        register_embedders()
        register_llms()
        register_rerankers()
        register_vlms()

    @staticmethod
    def create_embedder(name: str = "vllm", **kwargs):
        return embedder_registry.create(name, **kwargs)

    @staticmethod
    def create_llm(name: str = "vllm", **kwargs):
        return llm_registry.create(name, **kwargs)

    @staticmethod
    def create_reranker(name: str = "infinity", **kwargs):
        return reranker_registry.create(name, **kwargs)

    @staticmethod
    def create_vlm(name: str = "vllm", **kwargs):
        return vlm_registry.create(name, **kwargs)
