"""Stub :class:`TopicTagRepository`.

Topic/tag attachment per document is a future feature — useful for
faceted search and for "show me docs about X" UIs. No table exists
today.
"""

from __future__ import annotations

from openrag.core.ports.topic_tag_repo import TopicTagRepository
from openrag.services.persistence._stubs import _StubRepositoryBase, stub_not_implemented


class PgTopicTagRepository(_StubRepositoryBase, TopicTagRepository):
    """TODO: real impl once the ``topic_tags`` table is added."""

    async def bulk_insert(self, tags: list[dict]) -> int:
        raise stub_not_implemented("Topic / tag storage")

    async def get_by_document(self, document_id: str) -> list[dict]:
        raise stub_not_implemented("Topic / tag storage")

    async def delete_by_document(self, document_id: str) -> int:
        raise stub_not_implemented("Topic / tag storage")

    async def search(self, partition: str, tag: str, top_k: int = 10) -> list[dict]:
        raise stub_not_implemented("Topic / tag storage")


__all__ = ["PgTopicTagRepository"]
