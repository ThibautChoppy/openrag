"""
Text sanitization utilities for cleaning extracted text and improving quality.

This module provides functions to clean and normalize text extracted from various
document sources (PDFs, Office files, etc.) by removing excessive whitespace,
special characters, and other artifacts that don't add value.
"""

import re
import unicodedata


def sanitize_text(
    text: str,
    normalize_whitespace: bool = True,
    remove_control_chars: bool = True,
    remove_zero_width_chars: bool = True,
    max_consecutive_newlines: int = 2,
    normalize_unicode: bool = True,
) -> str:
    """
    Sanitize text by removing useless characters and normalizing whitespace.

    This function performs comprehensive text cleaning including:
    - Removing or normalizing control characters
    - Removing zero-width spaces and invisible characters
    - Normalizing excessive whitespace (spaces, tabs)
    - Limiting consecutive newlines
    - Unicode normalization

    Args:
        text: The input text to sanitize
        normalize_whitespace: If True, normalize spaces and tabs to single spaces
        remove_control_chars: If True, remove control characters (except \n, \r, \t)
        remove_zero_width_chars: If True, remove zero-width spaces and similar chars
        max_consecutive_newlines: Maximum number of consecutive newlines to keep (0 = unlimited)
        normalize_unicode: If True, normalize unicode to NFC form

    Returns:
        Sanitized text string

    Examples:
        >>> sanitize_text("Hello    world\\n\\n\\n\\nTest")
        'Hello world\\n\\nTest'
        >>> sanitize_text("Text with\\t\\ttabs")
        'Text with tabs'
    """
    if not text:
        return text

    # Normalize unicode to NFC form (composed form)
    if normalize_unicode:
        text = unicodedata.normalize("NFC", text)

    # Remove zero-width spaces and similar invisible characters
    if remove_zero_width_chars:
        # Zero-width space (U+200B), zero-width non-joiner (U+200C),
        # zero-width joiner (U+200D), word joiner (U+2060),
        # zero-width no-break space (U+FEFF)
        text = re.sub(r"[\u200B-\u200D\u2060\uFEFF]", "", text)

    # Remove control characters except newline, carriage return, and tab
    if remove_control_chars:
        # Remove C0 control characters (0x00-0x1F) except \t (0x09), \n (0x0A), \r (0x0D)
        # and C1 control characters (0x80-0x9F)
        text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]", "", text)

    # Normalize whitespace
    if normalize_whitespace:
        # Convert multiple spaces to single space
        text = re.sub(r" {2,}", " ", text)

        # Convert tabs to single space
        text = re.sub(r"\t+", " ", text)

        # Remove spaces at the beginning of lines
        text = re.sub(r"(?m)^ +", "", text)

        # Remove spaces at the end of lines
        text = re.sub(r"(?m) +$", "", text)

    # Normalize line breaks
    # First, normalize different line break styles to \n
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\r", "\n", text)

    # Limit consecutive newlines
    if max_consecutive_newlines > 0:
        pattern = r"\n{" + str(max_consecutive_newlines + 1) + r",}"
        replacement = "\n" * max_consecutive_newlines
        text = re.sub(pattern, replacement, text)

    # Remove leading/trailing whitespace
    text = text.strip()
    return text


# Control tokens a document could embed to forge citations or source boundaries:
# "[Source N]" (block marker), "[Sources: 1, 3]" / "Sources: 1, 3" (answer tag),
# and "----------" (separator). Neutralize them in untrusted text.
# Capture an optional trailing colon: dropping it also breaks the answer-tag form
# "[Sources: 1, 2]". The output parser's bracket is optional (\[?), so neutralizing
# only the "[" would leave "(Sources: 1, 2]" — still a parser match. Removing the
# colon defangs the keyword the parser keys on.
_INJECT_SOURCE_BLOCK_RE = re.compile(r"\[\s*(sources?)\b(\s*:)?", re.IGNORECASE)
_INJECT_SOURCES_TAG_RE = re.compile(r"(?im)^([ \t]*)(sources?)(\s*:\s*)(\[?[\d,\s]+\]?)[ \t]*$")
_INJECT_SEPARATOR_RE = re.compile(r"-{4,}")


def neutralize_prompt_control_tokens(text: str) -> str:
    """Defang RAG control tokens in untrusted text so they can't fake markers."""
    if not text:
        return text
    # "[Source...]" / "[Sources...]" -> open paren so it can't start a marker, and
    # drop any "[Sources:" colon so the answer-tag parser can't match the remainder.
    text = _INJECT_SOURCE_BLOCK_RE.sub(lambda m: "(" + m.group(1) + (" " if m.group(2) else ""), text)
    # Break the unbracketed "Sources: 1, 2" line form the parser also matches.
    text = _INJECT_SOURCES_TAG_RE.sub(r"\1\2 \4", text)
    # Cap long hyphen runs so they can't reproduce the separator.
    text = _INJECT_SEPARATOR_RE.sub("---", text)
    return text


def clean_markdown_table_spacing(markdown_table: str) -> str:
    """
    Normalize spacing inside a markdown table:
    - trims each cell
    - keeps table shape intact

    Args:
        markdown_table: Markdown table text to clean

    Returns:
        Cleaned markdown table with normalized spacing
    """
    cleaned_lines = []

    for line in markdown_table.strip().split("\n"):
        if "|" not in line:
            cleaned_lines.append(line.strip())
            continue

        # Split row into cells (preserve leading/trailing pipes)
        parts = line.split("|")

        # Strip each cell except the outer empty ones
        cleaned_cells = [cell.strip() for cell in parts]

        # Rebuild with a single space around each cell
        new_line = "| " + " | ".join(cleaned_cells[1:-1]) + " |"
        cleaned_lines.append(new_line)

    return "\n".join(cleaned_lines)


def sanitize_extracted_text(text: str) -> str:
    """
    Convenience function for sanitizing text extracted from documents.

    This applies a standard set of cleaning operations suitable for
    text extraction endpoints and general document processing.
    Uses the default sanitization settings which include:
    - Normalize whitespace
    - Remove control characters
    - Remove zero-width characters
    - Limit consecutive newlines to 2
    - Normalize Unicode

    Args:
        text: The extracted text to sanitize

    Returns:
        Sanitized text
    """
    return sanitize_text(text)
