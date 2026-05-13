"""Postgres persistence adapter — connection manager, schema, repositories.

This package contains the asyncpg-based Postgres adapter that replaces the
synchronous SQLAlchemy ORM in ``components/indexer/vectordb/utils.py``.

Public entry points:
    - :class:`connection.ConnectionManager` — pool lifecycle + migrations (7A.1)
    - :mod:`schema` — metadata-only Alembic target (7A.1)
    - Repositories (7A.2):
        - Real (decomposed from ``PartitionFileManager``):
            ``PgDocumentRepository``, ``PgUserRepository``,
            ``PgPartitionRepository``, ``PgOIDCSessionRepository``,
            ``PgWorkspaceRepository``.
        - Stubs (post-refactoring features — raise
          :class:`StubRepositoryError` on every call):
            ``PgJobRepository``, ``PgChunkRepository``,
            ``PgPromptRepository``, ``PgConversationRepository``,
            ``PgAuditLogRepository``, ``PgIdempotencyRepository``,
            ``PgEntityRepository``, ``PgTopicTagRepository``,
            ``PgModelEndpointRepository``, ``PgPresetRepository``.
"""

from openrag.services.persistence._stubs import StubRepositoryError
from openrag.services.persistence.audit_log_repo import PgAuditLogRepository
from openrag.services.persistence.chunk_repo import PgChunkRepository
from openrag.services.persistence.connection import ConnectionManager
from openrag.services.persistence.conversation_repo import PgConversationRepository
from openrag.services.persistence.document_repo import PgDocumentRepository
from openrag.services.persistence.entity_repo import PgEntityRepository
from openrag.services.persistence.idempotency_repo import PgIdempotencyRepository
from openrag.services.persistence.job_repo import PgJobRepository
from openrag.services.persistence.model_endpoint_repo import PgModelEndpointRepository
from openrag.services.persistence.oidc_session_repo import PgOIDCSessionRepository
from openrag.services.persistence.partition_repo import PgPartitionRepository
from openrag.services.persistence.preset_repo import PgPresetRepository
from openrag.services.persistence.prompt_repo import PgPromptRepository
from openrag.services.persistence.schema import metadata
from openrag.services.persistence.topic_tag_repo import PgTopicTagRepository
from openrag.services.persistence.user_repo import PgUserRepository
from openrag.services.persistence.workspace_repo import PgWorkspaceRepository

__all__ = [
    "ConnectionManager",
    "PgAuditLogRepository",
    "PgChunkRepository",
    "PgConversationRepository",
    "PgDocumentRepository",
    "PgEntityRepository",
    "PgIdempotencyRepository",
    "PgJobRepository",
    "PgModelEndpointRepository",
    "PgOIDCSessionRepository",
    "PgPartitionRepository",
    "PgPresetRepository",
    "PgPromptRepository",
    "PgTopicTagRepository",
    "PgUserRepository",
    "PgWorkspaceRepository",
    "StubRepositoryError",
    "metadata",
]
