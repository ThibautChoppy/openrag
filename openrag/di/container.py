"""Service container — wires registries and exposes component factories.

The container is the composition root for the refactored stack. It does
three things:

1. Populates the inference registries (Phase 6) so factory helpers can spin
   up embedders, LLMs, rerankers and VLMs by name.
2. Builds the storage adapters (Phase 7E) when a :class:`Settings` instance
   is supplied — a :class:`~core.ports.catalog_store.CatalogStore` and a
   :class:`~core.vector_stores.VectorStore`.
3. Owns the async :meth:`initialize` / :meth:`shutdown` lifecycle that opens
   and closes the asyncpg pool.

The ``settings`` argument is optional so the legacy test paths that only
care about registry side effects (``ServiceContainer()`` with no config)
keep working. Code that wants storage adapters must pass a
:class:`Settings` and ``await container.initialize()`` before issuing
queries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.embeddings import embedder_registry
from core.llm import llm_registry
from core.rerankers import reranker_registry
from core.vlm import vlm_registry
from di.embedders import register_embedders
from di.llms import register_llms
from di.repositories import create_catalog_store
from di.rerankers import register_rerankers
from di.vector_stores import create_vector_store
from di.vlms import register_vlms

if TYPE_CHECKING:
    from core.config.root import Settings
    from core.ports.audit_log_repo import AuditLogRepository
    from core.ports.catalog_store import CatalogStore
    from core.ports.chunk_repo import ChunkRepository
    from core.ports.conversation_repo import ConversationRepository
    from core.ports.document_repo import DocumentRepository
    from core.ports.entity_repo import EntityRepository
    from core.ports.idempotency_repo import IdempotencyRepository
    from core.ports.job_repo import JobRepository
    from core.ports.model_endpoint_repo import ModelEndpointRepository
    from core.ports.oidc_session_repo import OIDCSessionRepository
    from core.ports.partition_repo import PartitionRepository
    from core.ports.preset_repo import PresetRepository
    from core.ports.prompt_repo import PromptRepository
    from core.ports.topic_tag_repo import TopicTagRepository
    from core.ports.user_repo import UserRepository
    from core.ports.workspace_repo import WorkspaceRepository
    from core.vector_stores import VectorStore


_NO_SETTINGS_MESSAGE = (
    "ServiceContainer was constructed without a Settings instance — "
    "pass Settings to ServiceContainer(...) to wire storage adapters."
)


class ServiceContainer:
    """Populates registries and provides typed factory access."""

    def __init__(self, settings: Settings | None = None) -> None:
        register_embedders()
        register_llms()
        register_rerankers()
        register_vlms()

        self._settings = settings
        self._catalog_store: CatalogStore | None = (
            create_catalog_store(settings) if settings is not None else None
        )
        self._vector_store: VectorStore | None = (
            create_vector_store(settings) if settings is not None else None
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Open the storage adapters (asyncpg pool + Alembic migrations)."""
        if self._catalog_store is not None:
            await self._catalog_store.initialize()

    async def shutdown(self) -> None:
        """Close the storage adapters cleanly."""
        if self._catalog_store is not None:
            await self._catalog_store.shutdown()

    # ------------------------------------------------------------------
    # Storage adapters
    # ------------------------------------------------------------------

    @property
    def catalog_store(self) -> CatalogStore:
        if self._catalog_store is None:
            raise RuntimeError(_NO_SETTINGS_MESSAGE)
        return self._catalog_store

    @property
    def vector_store(self) -> VectorStore:
        """The Phase 7B :class:`MilvusVectorStore` built from settings.

        Cached at construction so repeated property reads return the same
        instance — every fresh build would open a new pymilvus gRPC
        channel.
        """
        if self._vector_store is None:
            raise RuntimeError(_NO_SETTINGS_MESSAGE)
        return self._vector_store

    # ------------------------------------------------------------------
    # Per-repo accessors (Phase 8 orchestrators take one repo, not the
    # whole store). All fifteen repos are exposed for symmetry and
    # grep-findability: shortcuts for the five real repos plus the ten
    # post-refactoring stubs.
    # ------------------------------------------------------------------

    @property
    def document_repo(self) -> DocumentRepository:
        return self.catalog_store.document_repo

    @property
    def user_repo(self) -> UserRepository:
        return self.catalog_store.user_repo

    @property
    def partition_repo(self) -> PartitionRepository:
        return self.catalog_store.partition_repo

    @property
    def oidc_session_repo(self) -> OIDCSessionRepository:
        return self.catalog_store.oidc_session_repo

    @property
    def workspace_repo(self) -> WorkspaceRepository:
        return self.catalog_store.workspace_repo

    @property
    def job_repo(self) -> JobRepository:
        return self.catalog_store.job_repo

    @property
    def chunk_repo(self) -> ChunkRepository:
        return self.catalog_store.chunk_repo

    @property
    def prompt_repo(self) -> PromptRepository:
        return self.catalog_store.prompt_repo

    @property
    def conversation_repo(self) -> ConversationRepository:
        return self.catalog_store.conversation_repo

    @property
    def audit_log_repo(self) -> AuditLogRepository:
        return self.catalog_store.audit_log_repo

    @property
    def idempotency_repo(self) -> IdempotencyRepository:
        return self.catalog_store.idempotency_repo

    @property
    def entity_repo(self) -> EntityRepository:
        return self.catalog_store.entity_repo

    @property
    def topic_tag_repo(self) -> TopicTagRepository:
        return self.catalog_store.topic_tag_repo

    @property
    def model_endpoint_repo(self) -> ModelEndpointRepository:
        return self.catalog_store.model_endpoint_repo

    @property
    def preset_repo(self) -> PresetRepository:
        return self.catalog_store.preset_repo

    # ------------------------------------------------------------------
    # Registry-based inference factories (Phase 6)
    # ------------------------------------------------------------------

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
