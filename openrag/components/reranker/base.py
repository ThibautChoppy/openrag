from abc import ABC, abstractmethod

from core.retrieval.rrf import rrf_reranking
from langchain_core.documents.base import Document


class BaseReranker(ABC):
    @abstractmethod
    async def rerank(self, query: str, documents: list[Document], top_k: int | None = None) -> list[Document]:
        """Rerank a list of documents based on a query and an optional top_k parameter"""

    @staticmethod
    def rrf_reranking(doc_lists: list[list[Document]], k: int = 60) -> list[Document]:
        return rrf_reranking(
            doc_lists,
            key_fn=lambda doc: doc.metadata.get("_id", id(doc)),
            k=k,
        )
