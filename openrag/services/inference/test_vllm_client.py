from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from core.utils.exceptions import (
    EmbeddingAPIError,
    EmbeddingResponseError,
    InferenceConnectionError,
    InferenceTimeoutError,
)

from .vllm_client import VLLMClient, VLLMEmbedder, VLLMVision

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chat_response(content: str = "hello") -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
    )


def _completions_response(text: str = "result") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"text": text}]})


def _embed_response(vectors: list[list[float]] | None = None) -> httpx.Response:
    vectors = vectors or [[0.1, 0.2, 0.3]]
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
    return httpx.Response(200, json={"data": data})


def _streaming_lines(tokens: list[str]) -> list[str]:
    lines = []
    for tok in tokens:
        chunk = {"choices": [{"delta": {"content": tok}}]}
        lines.append(f"data: {json.dumps(chunk)}")
    lines.append("data: [DONE]")
    return lines


# ---------------------------------------------------------------------------
# VLLMClient (LLM)
# ---------------------------------------------------------------------------


class TestVLLMClient:
    @pytest.fixture
    def client(self):
        c = VLLMClient(endpoint="http://vllm:8000/v1", model_name="mistral", api_key="k")
        yield c

    @pytest.mark.asyncio
    async def test_chat(self, client):
        transport = httpx.MockTransport(lambda req: _chat_response("world"))
        client._client = httpx.AsyncClient(transport=transport)
        result = await client.chat([{"role": "user", "content": "hi"}])
        assert result == "world"

    @pytest.mark.asyncio
    async def test_generate(self, client):
        transport = httpx.MockTransport(lambda req: _completions_response("done"))
        client._client = httpx.AsyncClient(transport=transport)
        result = await client.generate("say something")
        assert result == "done"

    @pytest.mark.asyncio
    async def test_chat_connection_error(self, client):
        async def fail(*a, **kw):
            raise httpx.ConnectError("refused")

        client._client = AsyncMock()
        client._client.post = fail
        with pytest.raises(InferenceConnectionError):
            await client.chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_chat_timeout(self, client):
        async def fail(*a, **kw):
            raise httpx.TimeoutException("timeout")

        client._client = AsyncMock()
        client._client.post = fail
        with pytest.raises(InferenceTimeoutError):
            await client.chat([{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_stream_chat(self, client):
        lines = _streaming_lines(["hel", "lo"])
        body = "\n".join(lines)
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text=body))
        client._client = httpx.AsyncClient(transport=transport)
        tokens = [t async for t in client.stream_chat([{"role": "user", "content": "hi"}])]
        assert tokens == ["hel", "lo"]

    @pytest.mark.asyncio
    async def test_stream_chat_http_error(self, client):
        transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
        client._client = httpx.AsyncClient(transport=transport)
        with pytest.raises(httpx.HTTPStatusError):
            async for _ in client.stream_chat([{"role": "user", "content": "hi"}]):
                pass

    @pytest.mark.asyncio
    async def test_defaults_forwarded(self, client):
        client._defaults = {"temperature": 0.5}
        captured = {}

        def capture(req):
            captured.update(json.loads(req.content))
            return _chat_response()

        transport = httpx.MockTransport(capture)
        client._client = httpx.AsyncClient(transport=transport)
        await client.chat([{"role": "user", "content": "hi"}])
        assert captured["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_kwargs_override_defaults(self, client):
        client._defaults = {"temperature": 0.5}
        captured = {}

        def capture(req):
            captured.update(json.loads(req.content))
            return _chat_response()

        transport = httpx.MockTransport(capture)
        client._client = httpx.AsyncClient(transport=transport)
        await client.chat([{"role": "user", "content": "hi"}], temperature=0.9)
        assert captured["temperature"] == 0.9

    @pytest.mark.asyncio
    async def test_trailing_slash_stripped(self):
        c = VLLMClient(endpoint="http://vllm:8000/v1/", model_name="m")
        assert c._endpoint == "http://vllm:8000/v1"
        await c.aclose()

    @pytest.mark.asyncio
    async def test_aclose(self, client):
        client._client = AsyncMock()
        await client.aclose()
        client._client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# VLLMEmbedder
# ---------------------------------------------------------------------------


class TestVLLMEmbedder:
    @pytest.fixture
    def embedder(self):
        return VLLMEmbedder(endpoint="http://vllm:8000/v1", model_name="bge-m3")

    @pytest.mark.asyncio
    async def test_embed(self, embedder):
        vecs = [[0.1, 0.2], [0.3, 0.4]]
        transport = httpx.MockTransport(lambda req: _embed_response(vecs))
        embedder._client = httpx.AsyncClient(transport=transport)
        result = await embedder.embed(["a", "b"])
        assert result == vecs
        assert embedder.dimension == 2

    @pytest.mark.asyncio
    async def test_embed_single(self, embedder):
        transport = httpx.MockTransport(lambda req: _embed_response([[1.0, 2.0, 3.0]]))
        embedder._client = httpx.AsyncClient(transport=transport)
        result = await embedder.embed_single("text")
        assert result == [1.0, 2.0, 3.0]

    @pytest.mark.asyncio
    async def test_embed_sorts_by_index(self, embedder):
        data = [{"index": 1, "embedding": [0.3]}, {"index": 0, "embedding": [0.1]}]
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": data}))
        embedder._client = httpx.AsyncClient(transport=transport)
        result = await embedder.embed(["a", "b"])
        assert result == [[0.1], [0.3]]

    @pytest.mark.asyncio
    async def test_embed_connection_error(self, embedder):
        async def fail(*a, **kw):
            raise httpx.ConnectError("refused")

        embedder._client = AsyncMock()
        embedder._client.post = fail
        with pytest.raises(EmbeddingAPIError):
            await embedder.embed(["text"])

    @pytest.mark.asyncio
    async def test_embed_timeout(self, embedder):
        async def fail(*a, **kw):
            raise httpx.TimeoutException("timeout")

        embedder._client = AsyncMock()
        embedder._client.post = fail
        with pytest.raises(EmbeddingAPIError):
            await embedder.embed(["text"])

    @pytest.mark.asyncio
    async def test_embed_bad_response_format(self, embedder):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"wrong": "shape"}))
        embedder._client = httpx.AsyncClient(transport=transport)
        with pytest.raises(EmbeddingResponseError):
            await embedder.embed(["text"])

    def test_dimension_unknown_raises(self, embedder):
        with pytest.raises(RuntimeError, match="Dimension unknown"):
            _ = embedder.dimension

    def test_dimension_from_constructor(self):
        e = VLLMEmbedder(endpoint="http://x", model_name="m", dimension=768)
        assert e.dimension == 768

    @pytest.mark.asyncio
    async def test_truncate_token_forwarded(self, embedder):
        captured = {}

        def capture(req):
            captured.update(json.loads(req.content))
            return _embed_response()

        transport = httpx.MockTransport(capture)
        embedder._client = httpx.AsyncClient(transport=transport)
        await embedder.embed(["x"])
        assert captured["truncate_prompt_tokens"] == 8192


# ---------------------------------------------------------------------------
# VLLMVision
# ---------------------------------------------------------------------------


class TestVLLMVision:
    @pytest.fixture
    def vlm(self):
        return VLLMVision(endpoint="http://vllm:8000/v1", model_name="qwen-vl")

    @pytest.mark.asyncio
    async def test_caption_image(self, vlm):
        transport = httpx.MockTransport(lambda req: _chat_response("a cat"))
        vlm._client = httpx.AsyncClient(transport=transport)
        result = await vlm.caption_image(b"\x89PNG\r\n", prompt="what is this?")
        assert result == "a cat"

    @pytest.mark.asyncio
    async def test_caption_sends_base64(self, vlm):
        captured = {}

        def capture(req):
            captured.update(json.loads(req.content))
            return _chat_response("ok")

        transport = httpx.MockTransport(capture)
        vlm._client = httpx.AsyncClient(transport=transport)
        await vlm.caption_image(b"img_data")
        content = captured["messages"][0]["content"]
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert content[1]["type"] == "text"

    @pytest.mark.asyncio
    async def test_caption_images_batch(self, vlm):
        transport = httpx.MockTransport(lambda req: _chat_response("desc"))
        vlm._client = httpx.AsyncClient(transport=transport)
        results = await vlm.caption_images_batch([b"a", b"b", b"c"])
        assert results == ["desc", "desc", "desc"]

    @pytest.mark.asyncio
    async def test_caption_connection_error(self, vlm):
        async def fail(*a, **kw):
            raise httpx.ConnectError("refused")

        vlm._client = AsyncMock()
        vlm._client.post = fail
        with pytest.raises(InferenceConnectionError):
            await vlm.caption_image(b"img")

    @pytest.mark.asyncio
    async def test_caption_timeout(self, vlm):
        async def fail(*a, **kw):
            raise httpx.TimeoutException("timeout")

        vlm._client = AsyncMock()
        vlm._client.post = fail
        with pytest.raises(InferenceTimeoutError):
            await vlm.caption_image(b"img")


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_llm_registered(self):
        from core.llm import llm_registry

        assert "vllm" in llm_registry

    def test_embedder_registered(self):
        from core.embeddings import embedder_registry

        assert "vllm" in embedder_registry

    def test_vlm_registered(self):
        from core.vlm import vlm_registry

        assert "vllm" in vlm_registry
