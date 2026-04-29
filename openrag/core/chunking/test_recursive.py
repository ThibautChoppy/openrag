"""End-to-end tests for the RecursiveSplitter chunker."""

from __future__ import annotations

from openrag.core.chunking.recursive import RecursiveSplitter
from openrag.core.chunking.registry import chunking_registry
from openrag.core.models.chunk import ChunkType
from openrag.core.models.document import ProcessedDocument, TextBlock


def _word_tokens(text: str) -> int:
    return len(text.split())


def test_recursive_splitter_is_registered():
    assert "recursive_splitter" in chunking_registry


def test_recursive_splitter_chunks_simple_document():
    splitter = RecursiveSplitter(chunk_size=10, chunk_overlap_rate=0.0, length_function=_word_tokens)
    doc = ProcessedDocument(
        document_id="d1",
        text_blocks=[TextBlock(text="alpha beta gamma\ndelta epsilon zeta\neta theta iota.", page_number=1)],
        metadata={"source": "test.md"},
    )
    chunks = splitter.chunk(doc, partition="p1")
    assert chunks
    assert all(c.partition == "p1" for c in chunks)
    assert all(c.document_id == "d1" for c in chunks)
    assert all(c.chunk_type == ChunkType.TEXT for c in chunks)


def test_recursive_splitter_emits_table_chunks():
    table = "| Col | Val |\n|-----|-----|\n" + "\n".join(f"| Group{i} | {' '.join(['x'] * 50)} |" for i in range(6))
    splitter = RecursiveSplitter(chunk_size=20, chunk_overlap_rate=0.0, length_function=_word_tokens)
    doc = ProcessedDocument(
        document_id="d1",
        text_blocks=[TextBlock(text=f"Some prose here.\n\n{table}\n\nMore prose.", page_number=1)],
    )
    chunks = splitter.chunk(doc, partition="p1")
    table_chunks = [c for c in chunks if c.chunk_type == ChunkType.TABLE]
    assert table_chunks, "expected at least one table-type chunk"


def test_recursive_splitter_skips_image_placeholder():
    placeholder_md = (
        "Real text first.\n\n<image_description>\n\n[Image Placeholder]\n\n</image_description>\n\nReal text after."
    )
    splitter = RecursiveSplitter(chunk_size=200, chunk_overlap_rate=0.0, length_function=_word_tokens)
    doc = ProcessedDocument(
        document_id="d1",
        text_blocks=[TextBlock(text=placeholder_md, page_number=1)],
    )
    chunks = splitter.chunk(doc, partition="p1")
    assert all(c.chunk_type != ChunkType.IMAGE_CAPTION for c in chunks)
    for c in chunks:
        assert "[image placeholder]" not in c.text.lower()


def test_recursive_splitter_metadata_passthrough():
    splitter = RecursiveSplitter(chunk_size=10, chunk_overlap_rate=0.0, length_function=_word_tokens)
    doc = ProcessedDocument(
        document_id="d1",
        text_blocks=[TextBlock(text="alpha beta gamma delta epsilon", page_number=1)],
        metadata={"source": "test.md", "filename": "test.md", "tag": "v1"},
    )
    chunks = splitter.chunk(doc, partition="p1")
    assert chunks
    assert chunks[0].metadata.get("source") == "test.md"
    assert chunks[0].metadata.get("tag") == "v1"


def test_recursive_splitter_empty_document_returns_empty():
    splitter = RecursiveSplitter(chunk_size=10, chunk_overlap_rate=0.0, length_function=_word_tokens)
    doc = ProcessedDocument(document_id="d1", text_blocks=[])
    assert splitter.chunk(doc, partition="p1") == []


def test_recursive_splitter_requires_length_function():
    import pytest

    with pytest.raises(ValueError, match="length_function"):
        RecursiveSplitter(chunk_size=10, chunk_overlap_rate=0.0)
