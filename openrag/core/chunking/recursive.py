"""Recursive markdown-aware chunking strategy.

Pure domain logic — no LLM client, no Ray, no LangChain ``Document``.
The token-counting function is injected (``length_function``); the actual
text splitter is ``langchain.text_splitter.RecursiveCharacterTextSplitter``,
a pure utility kept until a stdlib-only replacement is in place.

Contextualization (the LLM-driven [CONTEXT] block prepended to each chunk)
lives in ``core/indexing/contextualize.py`` (Phase 5D) and is applied as a
separate stage by the orchestrator — not from inside the chunker.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openrag.core.chunking.chunking_strategy import ChunkingStrategy
from openrag.core.chunking.markdown_utils import (
    MDElement,
    chunk_table,
    get_chunk_page_number,
    split_md_elements,
)
from openrag.core.chunking.registry import chunking_registry
from openrag.core.models.chunk import Chunk, ChunkType
from openrag.core.models.document import ProcessedDocument
from openrag.core.utils.text import sanitize_text

# Substring (case-insensitive) marking a "no useful content" image caption.
# Detection logic mirrors the legacy chunker, which skips these elements so
# they don't pollute the index.
_IMAGE_PLACEHOLDER_MARKER = "[image placeholder]"

# Tables/images smaller than this token count are inlined with surrounding
# text rather than emitted as standalone chunks.
_INLINE_ELEMENT_TOKEN_THRESHOLD = 100


class BaseChunker(ChunkingStrategy):
    """Base markdown-aware chunker.

    Subclasses must set ``self.text_splitter`` to an object with a
    ``.split_text(str) -> list[str]`` method (e.g. LangChain's
    ``RecursiveCharacterTextSplitter``).
    """

    def __init__(
        self,
        chunk_size: int = 200,
        chunk_overlap_rate: float = 0.2,
        length_function: Callable[[str], int] | None = None,
        **kwargs: Any,
    ) -> None:
        if length_function is None:
            raise ValueError("length_function is required (e.g. tokenizer.count_tokens)")
        self.chunk_size = chunk_size
        self.chunk_overlap_rate = chunk_overlap_rate
        self.chunk_overlap = int(self.chunk_size * self.chunk_overlap_rate)
        self.length_function = length_function
        self.text_splitter: Any = None

    # ------------------------------------------------------------------
    # ChunkingStrategy contract
    # ------------------------------------------------------------------
    def chunk(self, document: ProcessedDocument, partition: str = "default") -> list[Chunk]:
        """Split a processed document into ``Chunk`` objects."""
        content = self._content_from(document)
        if not content.strip():
            return []

        metadata = self._chunk_metadata_base(document, partition)
        md_chunks = self._get_chunks(content=content.strip(), metadata=metadata)

        return [
            Chunk(
                document_id=metadata.get("file_id", ""),
                text=md_chunks_meta["page_content"],
                chunk_index=i,
                chunk_type=ChunkType(md_chunks_meta["chunk_type"]),
                metadata={k: v for k, v in md_chunks_meta.items() if k not in ("page_content", "chunk_type", "page")},
                partition=partition,
                page_number=md_chunks_meta.get("page"),
            )
            for i, md_chunks_meta in enumerate(md_chunks)
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _content_from(document: ProcessedDocument) -> str:
        """Reconstruct chunkable markdown from a ProcessedDocument.

        Parsers that already produce a single text block containing the full
        markdown (with ``[PAGE_N]`` markers) flow through unchanged.
        Multi-block documents are joined with blank lines and synthetic page
        markers based on each block's ``page_number``.
        """
        if not document.text_blocks:
            return ""
        if len(document.text_blocks) == 1:
            return document.text_blocks[0].text

        parts: list[str] = []
        last_page: int | None = None
        for block in document.text_blocks:
            if block.page_number is not None and last_page is not None and block.page_number != last_page:
                parts.append(f"[PAGE_{last_page}]")
            parts.append(block.text)
            last_page = block.page_number
        return "\n\n".join(parts)

    @staticmethod
    def _chunk_metadata_base(document: ProcessedDocument, partition: str) -> dict[str, Any]:
        return {
            "file_id": document.document_id,
            "partition": partition,
            **document.metadata,
        }

    def split_text(self, text: str) -> list[str]:
        """Split a text string with the configured text splitter.

        Lazy-initializes a ``RecursiveCharacterTextSplitter`` if a subclass
        forgot to set one — preserves legacy behavior.
        """
        if self.text_splitter is None:
            from langchain.text_splitter import RecursiveCharacterTextSplitter

            self.text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                length_function=self.length_function,
            )
        return self.text_splitter.split_text(text)

    def _prepare_md_elements(self, content: str) -> tuple[list[MDElement], list[MDElement]]:
        """Separate markdown into (inline-able texts) and (standalone tables/images)."""
        md_elements = split_md_elements(content)
        tables_and_images: list[MDElement] = []
        texts: list[MDElement] = []

        for element in md_elements:
            if element.type in ("table", "image"):
                if element.type == "image" and _IMAGE_PLACEHOLDER_MARKER in element.content.lower():
                    continue
                if self.length_function(element.content) <= _INLINE_ELEMENT_TOKEN_THRESHOLD:
                    texts.append(element)
                else:
                    tables_and_images.append(element)
            else:
                texts.append(element)

        return texts, tables_and_images

    def _get_chunks(self, content: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
        """Produce per-chunk dicts with ``page_content`` + metadata fields.

        The dict shape is intentional — it lets ``chunk()`` build ``Chunk``
        objects without leaking domain types into the lower-level helpers.
        """
        texts, tables_and_images = self._prepare_md_elements(content=content)
        combined_texts = "\n".join(e.content for e in texts)

        sanitized = sanitize_text(
            combined_texts,
            normalize_whitespace=True,
            remove_control_chars=True,
            remove_zero_width_chars=True,
            max_consecutive_newlines=2,
            normalize_unicode=True,
        )
        text_chunks = self.split_text(sanitized)

        chunks: list[dict[str, Any]] = []

        for element in tables_and_images:
            if element.type == "table" and self.length_function(element.content) > self.chunk_size:
                subtables = chunk_table(
                    table_element=element,
                    chunk_size=self.chunk_size,
                    length_function=self.length_function,
                )
                chunks.extend(
                    {
                        "page_content": subtable.content.strip(),
                        "page": subtable.page_number,
                        "chunk_type": "table",
                        **metadata,
                    }
                    for subtable in subtables
                )
            else:
                chunks.append(
                    {
                        "page_content": element.content.strip(),
                        "page": element.page_number,
                        "chunk_type": element.type,
                        **metadata,
                    }
                )

        prev_page = 1
        for c in text_chunks:
            page_info = get_chunk_page_number(chunk_str=c, previous_chunk_ending_page=prev_page)
            prev_page = page_info["end_page"]
            chunks.append(
                {
                    "page_content": c.strip(),
                    "page": page_info["start_page"],
                    "chunk_type": "text",
                    **metadata,
                }
            )

        if not chunks:
            return []
        chunks.sort(key=lambda d: d.get("page") or 0)
        return chunks


@chunking_registry.register("recursive_splitter")
class RecursiveSplitter(BaseChunker):
    """Markdown-aware chunker backed by ``RecursiveCharacterTextSplitter``.

    Splits on paragraph boundaries first, then sentence terminators, then
    smaller separators.
    """

    def __init__(
        self,
        chunk_size: int = 200,
        chunk_overlap_rate: float = 0.2,
        length_function: Callable[[str], int] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            chunk_size=chunk_size,
            chunk_overlap_rate=chunk_overlap_rate,
            length_function=length_function,
            **kwargs,
        )
        from langchain.text_splitter import RecursiveCharacterTextSplitter

        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            length_function=self.length_function,
            is_separator_regex=True,
            separators=["\n", r"(?<=[\.\?\!])"],
        )
