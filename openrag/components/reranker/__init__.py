import asyncio

import services.inference.reranker_clients  # noqa: F401 — registers "infinity"/"openai"
from core.config.retrieval import RerankerConfig
from core.rerankers import reranker_registry

from .base import BaseReranker


class _RerankerShim(BaseReranker):
    """Wraps a core ``Reranker`` (str-in / (idx, score)-out) behind the
    legacy ``BaseReranker`` interface (Document-in / Document-out)."""

    def __init__(self, delegate, semaphore: int = 3):
        self._delegate = delegate
        self._semaphore = asyncio.Semaphore(semaphore)

    async def rerank(self, query, documents, top_k=None):
        async with self._semaphore:
            texts = [doc.page_content for doc in documents]
            ranked = await self._delegate.rerank(query, texts, top_k=top_k)
            output = []
            for index, score in ranked:
                if not 0 <= index < len(documents):
                    continue
                doc = documents[index]
                doc.metadata["relevance_score"] = score
                output.append(doc)
            return output


class RerankerFactory:
    @staticmethod
    def get_reranker(reranker_config: RerankerConfig) -> BaseReranker:
        provider = reranker_config.provider
        delegate = reranker_registry.create(
            provider,
            endpoint=reranker_config.base_url,
            model_name=reranker_config.model_name,
            api_key=reranker_config.api_key,
            timeout=reranker_config.timeout,
        )
        return _RerankerShim(delegate, semaphore=reranker_config.semaphore)
