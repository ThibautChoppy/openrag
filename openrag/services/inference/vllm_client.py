"""vLLM / OpenAI-compatible inference clients.

Three classes grouped by server — all talk to the same OpenAI-compatible API:

* ``VLLMClient``   → ``LLM``      (chat completions)
* ``VLLMEmbedder`` → ``Embedder``  (embeddings)
* ``VLLMVision``   → ``VLM``       (image captioning via chat completions)

Each class has its own circuit breaker instance so an embedder outage
doesn't trip the LLM breaker.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator

import httpx
from core.embeddings import Embedder, embedder_registry
from core.llm import LLM, llm_registry
from core.utils.exceptions import (
    EmbeddingAPIError,
    EmbeddingResponseError,
    InferenceConnectionError,
    InferenceTimeoutError,
)
from core.vlm import VLM, vlm_registry
from utils.logger import get_logger

from ._circuit_breaker import with_circuit_breaker
from ._retry import with_retry

logger = get_logger()


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


@llm_registry.register("vllm")
class VLLMClient(LLM):
    """OpenAI-compatible LLM client backed by vLLM."""

    def __init__(
        self,
        endpoint: str,
        model_name: str,
        *,
        api_key: str = "",
        timeout: float = 240.0,
        **kwargs,
    ):
        self._endpoint = endpoint.rstrip("/")
        self._model = model_name
        self._defaults = kwargs
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)

    @with_circuit_breaker("llm")
    @with_retry(max_attempts=3)
    async def generate(self, prompt: str, **kwargs) -> str:
        payload = {"model": self._model, "prompt": prompt, **self._defaults, **kwargs}
        try:
            resp = await self._client.post(f"{self._endpoint}/completions", json=payload)
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise InferenceConnectionError(f"Cannot reach LLM at {self._endpoint}") from exc
        except httpx.TimeoutException as exc:
            raise InferenceTimeoutError(f"LLM request timed out at {self._endpoint}") from exc
        return resp.json()["choices"][0]["text"]

    @with_circuit_breaker("llm")
    @with_retry(max_attempts=3)
    async def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        payload = {"model": self._model, "messages": messages, **self._defaults, **kwargs, "stream": False}
        try:
            resp = await self._client.post(f"{self._endpoint}/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise InferenceConnectionError(f"Cannot reach LLM at {self._endpoint}") from exc
        except httpx.TimeoutException as exc:
            raise InferenceTimeoutError(f"LLM request timed out at {self._endpoint}") from exc
        return resp.json()["choices"][0]["message"]["content"]

    async def stream_chat(self, messages: list[dict[str, str]], **kwargs) -> AsyncIterator[str]:
        payload = {"model": self._model, "messages": messages, "stream": True, **self._defaults, **kwargs}
        async with self._client.stream("POST", f"{self._endpoint}/chat/completions", json=payload) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise httpx.HTTPStatusError(
                    f"LLM streaming error ({resp.status_code}): {resp.text}",
                    request=resp.request,
                    response=resp,
                )
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content

    async def aclose(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Embedder
# ---------------------------------------------------------------------------


@embedder_registry.register("vllm")
class VLLMEmbedder(Embedder):
    """OpenAI-compatible embedding client backed by vLLM."""

    def __init__(
        self,
        endpoint: str,
        model_name: str,
        *,
        api_key: str = "",
        max_model_len: int = 8192,
        timeout: float = 60.0,
        dimension: int | None = None,
        **_kwargs,
    ):
        self._endpoint = endpoint.rstrip("/")
        self._model = model_name
        self._max_model_len = max_model_len
        self._dimension: int | None = dimension
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)

    @with_circuit_breaker("embedder")
    @with_retry(max_attempts=3)
    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = await self._client.post(
                f"{self._endpoint}/embeddings",
                json={
                    "model": self._model,
                    "input": texts,
                    "truncate_prompt_tokens": self._max_model_len,
                },
            )
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise EmbeddingAPIError(
                f"Cannot reach embedder at {self._endpoint}",
                model_name=self._model,
                base_url=self._endpoint,
                error=str(exc),
            ) from exc
        except httpx.TimeoutException as exc:
            raise EmbeddingAPIError(
                f"Embedder request timed out at {self._endpoint}",
                model_name=self._model,
                base_url=self._endpoint,
                error=str(exc),
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise EmbeddingAPIError(
                f"Embedder API error ({exc.response.status_code})",
                model_name=self._model,
                base_url=self._endpoint,
                error=exc.response.text,
            ) from exc

        try:
            data = resp.json()["data"]
            embeddings = [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]
        except (KeyError, IndexError, TypeError) as exc:
            raise EmbeddingResponseError(
                "Unexpected embedding response format",
                model_name=self._model,
                base_url=self._endpoint,
                error=str(exc),
            ) from exc

        if self._dimension is None and embeddings:
            self._dimension = len(embeddings[0])
        return embeddings

    async def embed_single(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0]

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError("Dimension unknown — call embed() first or pass dimension to constructor")
        return self._dimension

    async def aclose(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# VLM
# ---------------------------------------------------------------------------


@vlm_registry.register("vllm")
class VLLMVision(VLM):
    """OpenAI-compatible vision client backed by vLLM."""

    def __init__(
        self,
        endpoint: str,
        model_name: str,
        *,
        api_key: str = "",
        timeout: float = 60.0,
        **_kwargs,
    ):
        self._endpoint = endpoint.rstrip("/")
        self._model = model_name
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(timeout=timeout, headers=headers)

    @with_circuit_breaker("vlm")
    @with_retry(max_attempts=2)
    async def caption_image(self, image_bytes: bytes, prompt: str | None = None) -> str:
        image_b64 = base64.b64encode(image_bytes).decode()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                    {"type": "text", "text": prompt or "Describe this image in detail."},
                ],
            }
        ]
        try:
            resp = await self._client.post(
                f"{self._endpoint}/chat/completions",
                json={"model": self._model, "messages": messages, "max_tokens": 1024},
            )
            resp.raise_for_status()
        except httpx.ConnectError as exc:
            raise InferenceConnectionError(f"Cannot reach VLM at {self._endpoint}") from exc
        except httpx.TimeoutException as exc:
            raise InferenceTimeoutError(f"VLM request timed out at {self._endpoint}") from exc
        return resp.json()["choices"][0]["message"]["content"]

    async def caption_images_batch(self, images: list[bytes], prompt: str | None = None) -> list[str]:
        tasks = [asyncio.create_task(self.caption_image(img, prompt)) for img in images]
        try:
            return list(await asyncio.gather(*tasks))
        except Exception:
            for t in tasks:
                t.cancel()
            raise

    async def aclose(self) -> None:
        await self._client.aclose()
