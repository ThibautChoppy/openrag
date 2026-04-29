"""Tests for core.chunking.markdown_utils.

Mirrors components/indexer/chunker/test_chunking.py to verify behavior is
preserved through the move into core/.
"""

from __future__ import annotations

from openrag.core.chunking.markdown_utils import (
    MDElement,
    chunk_table,
    get_chunk_page_number,
    split_md_elements,
)


def _mock_length(text: str) -> int:
    """Estimate token count at ~4 chars per token (matches legacy tests)."""
    return len(text) // 4


class TestSplitMdElements:
    def test_simple_text_only(self):
        md = "This is a simple paragraph.\n\nAnother paragraph here."
        elems = split_md_elements(md)
        assert len(elems) == 1
        assert elems[0].type == "text"
        assert elems[0].content == md

    def test_single_table(self):
        md = (
            "Some text before.\n\n| Header 1 | Header 2 |\n|----------|----------|\n"
            "| Cell 1   | Cell 2   |\n| Cell 3   | Cell 4   |\n\nSome text after."
        )
        elems = split_md_elements(md)
        assert [e.type for e in elems] == ["text", "table", "text"]
        assert "Header 1" in elems[1].content

    def test_single_image(self):
        md = (
            "\nText before image.\n\n<image_description>\nA beautiful sunset over the ocean.\n"
            "</image_description>\n\nText after image."
        )
        elems = split_md_elements(md)
        assert [e.type for e in elems] == ["text", "image", "text"]
        assert "sunset" in elems[1].content

    def test_table_inside_image_description_is_ignored(self):
        md = (
            "\n<image_description>\nThis image contains a table:\n| Col 1 | Col 2 |\n"
            "|-------|-------|\n| A     | B     |\n</image_description>\n\n"
            "Outside table:\n| Real 1 | Real 2 |\n|--------|--------|\n| X      | Y      |\n"
        )
        elems = split_md_elements(md)
        tables = [e for e in elems if e.type == "table"]
        assert len(tables) == 1
        assert "Real 1" in tables[0].content

    def test_page_markers_with_table(self):
        md = (
            "text on page 1.\n[PAGE_1]\nText on page 2.\n\n"
            "| Header 1 | Header 2 |\n|----------|----------|\n| Data 1   | Data 2   |\n\n"
            "[PAGE_2]\nMore content.\n"
        )
        elems = split_md_elements(md)
        tables = [e for e in elems if e.type == "table"]
        assert len(tables) == 1
        assert tables[0].page_number == 2

    def test_page_markers_with_images(self):
        md = "\n[PAGE_1]\n[PAGE_2]\n<image_description>\nImage on page 3.\n</image_description>\n"
        elems = split_md_elements(md)
        images = [e for e in elems if e.type == "image"]
        assert len(images) == 1
        assert images[0].page_number == 3


class TestGetChunkPageNumber:
    def test_no_markers_returns_previous_page(self):
        result = get_chunk_page_number("Just some plain text content.", previous_chunk_ending_page=1)
        assert result == {"start_page": 1, "end_page": 1}

    def test_chunk_starts_with_marker(self):
        result = get_chunk_page_number("[PAGE_2]Content on page 3.", previous_chunk_ending_page=1)
        assert result == {"start_page": 3, "end_page": 3}

    def test_chunk_ends_with_marker(self):
        result = get_chunk_page_number("Content on page 1.[PAGE_1]", previous_chunk_ending_page=1)
        assert result == {"start_page": 1, "end_page": 1}

    def test_marker_in_middle(self):
        result = get_chunk_page_number("Start on page 1.[PAGE_1]End on page 2.", previous_chunk_ending_page=1)
        assert result == {"start_page": 1, "end_page": 2}


class TestChunkTable:
    def test_small_table_no_chunking(self):
        content = "| Name | Age |\n|------|-----|\n| John | 30 |\n| Jane | 25 |"
        elem = MDElement(type="table", content=content, page_number=1)
        chunks = chunk_table(elem, chunk_size=1000, length_function=_mock_length)
        assert len(chunks) == 1
        assert chunks[0].type == "table"
        assert chunks[0].page_number == 1
        assert "John" in chunks[0].content
        assert "Jane" in chunks[0].content

    def test_chunking_preserves_groups(self):
        header = "| Country | Strategy | Goals |"
        g1 = "| USA     | Cyber    | Goal 1 |\n|         |          | Goal 2 |\n|         |          | Goal 3 |"
        g2 = "| Mexico  | Defense  | Goal X |\n|         |          | Goal Y |\n|         |          | Goal Z |"
        table = f"{header}\n|----|----|----|\n{g1}\n{g2}\n"
        elem = MDElement(type="table", content=table, page_number=2)
        chunk_size = _mock_length(table) // 2
        chunks = chunk_table(elem, chunk_size=chunk_size, length_function=_mock_length)
        assert len(chunks) == 2
        assert all(c.type == "table" for c in chunks)
        assert all(header in c.content for c in chunks)
        assert "USA" in chunks[0].content
