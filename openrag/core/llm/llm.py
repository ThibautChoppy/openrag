"""Abstract LLM interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class LLM(ABC):
    """Base class for all LLM providers."""

    @abstractmethod
    async def generate(self, prompt: str, **kwargs) -> str:
        """Generate a completion for a prompt."""
        ...

    @abstractmethod
    async def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        """Chat completion with message list."""
        ...

    async def stream_chat(self, messages: list[dict[str, str]], **kwargs) -> AsyncIterator[str]:
        """Stream chat completion. Default falls back to non-streaming."""
        result = await self.chat(messages, **kwargs)
        yield result
