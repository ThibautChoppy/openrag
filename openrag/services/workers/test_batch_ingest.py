from __future__ import annotations

import asyncio

import pytest
from core.models.chunk import Chunk
from core.models.document import Document, DocumentType, ProcessedDocument, TextBlock
from services.workers.batch_ingest import ingest_batch
from services.workers.pipeline_builder import build_indexing_pipeline
from services.workers.result_aggregation import aggregate_batch_results

# ---------------------------------------------------------------------------
# Fakes (minimal — no ABC inheritance needed for these tests)
# ---------------------------------------------------------------------------


class FakeParser:
    def __init__(self, processed: ProcessedDocument) -> None:
        self.processed = processed

    async def parse(self, document: Document) -> ProcessedDocument:
        return self.processed

    def supported_types(self) -> list[str]:
        return [DocumentType.TEXT.value]


class _FailParser:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def parse(self, document: Document) -> ProcessedDocument:
        raise self.error

    def supported_types(self) -> list[str]:
        return [DocumentType.TEXT.value]


class FakeChunker:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks

    def chunk(self, document: ProcessedDocument, partition: str = "default") -> list[Chunk]:
        return self.chunks


class FakeEmbedder:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return self.vectors[: len(texts)]


class FakeVectorStore:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def upsert(self, chunks: list[Chunk], collection: str = "default") -> int:
        self.calls.append((chunks, collection))
        return len(chunks)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc(filename: str = "doc.txt") -> Document:
    return Document(filename=filename, text="hello", partition="p")


def _make_processed(doc: Document) -> ProcessedDocument:
    return ProcessedDocument(document_id=doc.id, text_blocks=[TextBlock(text="hello")])


def _make_chunk(doc: Document) -> Chunk:
    return Chunk(id=doc.id, text="hello", partition="p")


def _make_pipeline(doc: Document, *, fail: bool = False) -> tuple:
    processed = _make_processed(doc)
    chunk = _make_chunk(doc)
    parser = _FailParser(RuntimeError("parse error")) if fail else FakeParser(processed)
    chunker = FakeChunker([chunk])
    embedder = FakeEmbedder([[1.0]])
    vector_store = FakeVectorStore()
    pipeline = build_indexing_pipeline(
        parser=parser,
        chunker=chunker,
        embedder=embedder,
        vector_store=vector_store,
    )
    return pipeline, vector_store


# ---------------------------------------------------------------------------
# Tests — ingest_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_batch_all_succeed():
    docs = [_make_doc(f"doc{i}.txt") for i in range(3)]
    pipeline, store = _make_pipeline(docs[0])
    rows = [{"document": doc, "partition": "p"} for doc in docs]

    result = await ingest_batch(pipeline, rows)

    assert len(result) == 3
    assert all(r["stage"] == "stored" for r in result)
    assert all(r["stored_count"] == 1 for r in result)


@pytest.mark.asyncio
async def test_ingest_batch_partial_failure_does_not_abort_others():
    docs = [_make_doc(f"doc{i}.txt") for i in range(3)]
    processed = _make_processed(docs[0])
    chunk = _make_chunk(docs[0])

    # Parser fails on the *second* call only
    call_count = 0

    class SelectiveParser:
        async def parse(self, document: Document) -> ProcessedDocument:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("second parse fails")
            return processed

        def supported_types(self) -> list[str]:
            return [DocumentType.TEXT.value]

    pipeline = build_indexing_pipeline(
        parser=SelectiveParser(),
        chunker=FakeChunker([chunk]),
        embedder=FakeEmbedder([[1.0]]),
        vector_store=FakeVectorStore(),
    )
    rows = [{"document": doc, "partition": "p"} for doc in docs]

    result = await ingest_batch(pipeline, rows)

    stages = [r["stage"] for r in result]
    assert stages.count("stored") == 2
    assert stages.count("parse_failed") == 1
    failed_row = next(r for r in result if r["stage"] == "parse_failed")
    assert failed_row["error"] == "second parse fails"


@pytest.mark.asyncio
async def test_ingest_batch_all_fail():
    docs = [_make_doc(f"doc{i}.txt") for i in range(2)]
    pipeline, _ = _make_pipeline(docs[0], fail=True)
    rows = [{"document": doc, "partition": "p"} for doc in docs]

    result = await ingest_batch(pipeline, rows)

    assert all(r["stage"] == "parse_failed" for r in result)


@pytest.mark.asyncio
async def test_ingest_batch_returns_same_row_objects():
    doc = _make_doc()
    pipeline, _ = _make_pipeline(doc)
    row: dict = {"document": doc, "partition": "p"}

    result = await ingest_batch(pipeline, [row])

    assert result[0] is row


@pytest.mark.asyncio
async def test_ingest_batch_concurrency_cap_limits_parallelism():
    """At most *concurrency* rows run simultaneously."""
    active: list[int] = []
    peak: list[int] = []

    class SlowParser:
        async def parse(self, document: Document) -> ProcessedDocument:
            active.append(1)
            peak.append(len(active))
            await asyncio.sleep(0)
            active.pop()
            return ProcessedDocument(document_id=document.id, text_blocks=[TextBlock(text="x")])

        def supported_types(self) -> list[str]:
            return [DocumentType.TEXT.value]

    doc = _make_doc()
    chunk = _make_chunk(doc)
    pipeline = build_indexing_pipeline(
        parser=SlowParser(),
        chunker=FakeChunker([chunk]),
        embedder=FakeEmbedder([[1.0]]),
        vector_store=FakeVectorStore(),
    )
    rows = [{"document": _make_doc(f"d{i}.txt"), "partition": "p"} for i in range(5)]

    await ingest_batch(pipeline, rows, concurrency=2)

    assert max(peak) <= 2


@pytest.mark.asyncio
async def test_ingest_batch_empty_input_returns_empty_list():
    pipeline, _ = _make_pipeline(_make_doc())
    result = await ingest_batch(pipeline, [])
    assert result == []


# ---------------------------------------------------------------------------
# Tests — aggregate_batch_results
# ---------------------------------------------------------------------------


def test_aggregate_all_succeeded():
    rows = [
        {"stage": "stored", "stored_count": 3},
        {"stage": "stored", "stored_count": 2},
    ]
    summary = aggregate_batch_results(rows)

    assert summary.total == 2
    assert summary.succeeded == 2
    assert summary.failed == 0
    assert summary.stored_count == 5
    assert summary.failures == ()
    assert summary.success_rate == 1.0


def test_aggregate_mixed_results():
    rows = [
        {"stage": "stored", "stored_count": 4},
        {"stage": "embed_failed", "error": "timeout"},
        {"stage": "chunk_failed", "error": "empty doc"},
    ]
    summary = aggregate_batch_results(rows)

    assert summary.total == 3
    assert summary.succeeded == 1
    assert summary.failed == 2
    assert summary.stored_count == 4
    assert len(summary.failures) == 2
    assert {f.stage for f in summary.failures} == {"embed_failed", "chunk_failed"}


def test_aggregate_all_failed():
    rows = [
        {"stage": "parse_failed", "error": "bad file"},
        {"stage": "parse_failed", "error": "another bad file"},
    ]
    summary = aggregate_batch_results(rows)

    assert summary.succeeded == 0
    assert summary.failed == 2
    assert summary.stored_count == 0
    assert summary.success_rate == 0.0


def test_aggregate_empty_input():
    summary = aggregate_batch_results([])

    assert summary.total == 0
    assert summary.succeeded == 0
    assert summary.failed == 0
    assert summary.stored_count == 0
    assert summary.success_rate == 0.0


def test_aggregate_missing_stage_counts_as_failure():
    rows = [{"stored_count": 1}]  # no "stage" key
    summary = aggregate_batch_results(rows)

    assert summary.failed == 1
    assert summary.succeeded == 0


@pytest.mark.asyncio
async def test_ingest_then_aggregate_round_trip():
    docs = [_make_doc(f"doc{i}.txt") for i in range(4)]
    processed = _make_processed(docs[0])

    call_count = 0

    class SelectiveParser:
        async def parse(self, document: Document) -> ProcessedDocument:
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise RuntimeError("oops")
            return processed

        def supported_types(self) -> list[str]:
            return [DocumentType.TEXT.value]

    chunk = _make_chunk(docs[0])
    pipeline = build_indexing_pipeline(
        parser=SelectiveParser(),
        chunker=FakeChunker([chunk]),
        embedder=FakeEmbedder([[1.0]]),
        vector_store=FakeVectorStore(),
    )
    rows = [{"document": doc, "partition": "p"} for doc in docs]

    result = await ingest_batch(pipeline, rows)
    summary = aggregate_batch_results(result)

    assert summary.total == 4
    assert summary.succeeded == 3
    assert summary.failed == 1
    assert summary.stored_count == 3
