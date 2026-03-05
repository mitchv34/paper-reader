"""Core PDF-to-markdown converter wrapping marker-pdf with section-aware chunking."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Lazy imports for marker (heavy PyTorch deps)
_converter = None
_artifact_dict = None


def _parse_page_range(page_range: str) -> list[int]:
    """Parse a page range string like '0-4,8,10-12' into a sorted list of ints."""
    pages: list[int] = []
    for part in page_range.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            pages.extend(range(int(start), int(end) + 1))
        else:
            pages.append(int(part))
    return sorted(set(pages))


def _get_converter(output_format: str = "markdown", page_range: str | None = None):
    """Lazy-load marker models on first call."""
    global _converter, _artifact_dict
    if _artifact_dict is None:
        from marker.models import create_model_dict

        _artifact_dict = create_model_dict()

    from marker.converters.pdf import PdfConverter

    config: dict = {"output_format": output_format}
    if page_range:
        config["page_range"] = _parse_page_range(page_range)
    return PdfConverter(artifact_dict=_artifact_dict, config=config)


# ---------------------------------------------------------------------------
# Section tree
# ---------------------------------------------------------------------------

@dataclass
class Section:
    """A section/subsection node in the document tree."""

    title: str
    level: int  # 1 = #, 2 = ##, 3 = ###, etc.
    content: str = ""  # text body (excluding child sections)
    children: list["Section"] = field(default_factory=list)
    page_hint: Optional[int] = None  # approximate page if detectable

    def flatten(self, prefix: str = "") -> list[dict]:
        """Flatten tree into a list of dicts with full section path."""
        path = f"{prefix} > {self.title}".strip(" >") if prefix else self.title
        items = []
        if self.content.strip():
            items.append({
                "section_path": path,
                "level": self.level,
                "title": self.title,
                "content": self.content.strip(),
            })
        for child in self.children:
            items.extend(child.flatten(prefix=path))
        return items


def _parse_sections(markdown: str) -> list[Section]:
    """Parse markdown into a section tree using header hierarchy.

    Returns a list of top-level sections. Content before the first header
    becomes a synthetic "Preamble" section.
    """
    lines = markdown.split("\n")
    header_re = re.compile(r"^(#{1,6})\s+(.+)$")

    # Collect (level, title, line_index) for every header
    headers: list[tuple[int, str, int]] = []
    for i, line in enumerate(lines):
        m = header_re.match(line)
        if m:
            headers.append((len(m.group(1)), m.group(2).strip(), i))

    if not headers:
        # No headers at all, return everything as one section
        return [Section(title="Document", level=1, content=markdown)]

    # Build flat list of (section, level) with their text blocks
    flat: list[Section] = []

    # Preamble: text before first header
    preamble_lines = lines[: headers[0][2]]
    preamble_text = "\n".join(preamble_lines).strip()
    if preamble_text:
        flat.append(Section(title="Preamble", level=0, content=preamble_text))

    for idx, (level, title, line_idx) in enumerate(headers):
        # Text runs from the line after this header to the line before the next header
        end = headers[idx + 1][2] if idx + 1 < len(headers) else len(lines)
        body = "\n".join(lines[line_idx + 1 : end]).strip()
        flat.append(Section(title=title, level=level, content=body))

    # Build tree: nest sections under their parent based on level
    root_sections: list[Section] = []
    stack: list[Section] = []  # stack of ancestors

    for sec in flat:
        # Pop stack until we find a parent with lower level
        while stack and stack[-1].level >= sec.level:
            stack.pop()

        if stack:
            # Move this section's content under the parent, as a child
            stack[-1].children.append(sec)
        else:
            root_sections.append(sec)

        stack.append(sec)

    return root_sections


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Rough token estimate. ~4 chars per token for English."""
    return max(1, len(text) // 4)


def _split_text(text: str, max_tokens: int) -> list[str]:
    """Split text into pieces respecting paragraph boundaries."""
    if _estimate_tokens(text) <= max_tokens:
        return [text]

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = _estimate_tokens(para)
        if current and current_tokens + para_tokens > max_tokens:
            chunks.append("\n\n".join(current))
            current = [para]
            current_tokens = para_tokens
        else:
            current.append(para)
            current_tokens += para_tokens

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _chunk_sections(
    sections: list[Section], max_tokens: int = 2000
) -> list[dict]:
    """Convert section tree into flat chunks with metadata."""
    flat_sections = []
    for sec in sections:
        flat_sections.extend(sec.flatten())

    chunks: list[dict] = []
    chunk_id = 0

    for sec_info in flat_sections:
        pieces = _split_text(sec_info["content"], max_tokens)
        for i, piece in enumerate(pieces):
            chunks.append({
                "chunk_id": chunk_id,
                "section_path": sec_info["section_path"],
                "section_title": sec_info["title"],
                "section_level": sec_info["level"],
                "part": i + 1 if len(pieces) > 1 else None,
                "total_parts": len(pieces) if len(pieces) > 1 else None,
                "content": piece,
                "token_estimate": _estimate_tokens(piece),
            })
            chunk_id += 1

    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert_pdf(
    file_path: str,
    output_format: str = "markdown",
    page_range: str | None = None,
) -> dict:
    """Convert a PDF to markdown (or html/json).

    Args:
        file_path: Path to the PDF file.
        output_format: "markdown", "html", or "json".
        page_range: Optional page range, e.g. "0-4" for first 5 pages (0-indexed).

    Returns:
        {
            "text": "...",
            "format": "md" | "html" | "json",
            "images": {...},
            "source_path": "...",
        }
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    converter = _get_converter(output_format, page_range=page_range)
    rendered = converter(str(path))

    from marker.output import text_from_rendered

    text, ext, images = text_from_rendered(rendered)

    return {
        "text": text,
        "format": ext,
        "images": {k: "(base64 image)" for k in images},  # don't serialize full images
        "source_path": str(path),
    }


def convert_to_chunks(
    file_path: str,
    max_tokens: int = 2000,
    page_range: str | None = None,
) -> dict:
    """Convert a PDF to chunked RAG format with section hierarchy.

    Returns:
        {
            "metadata": { "source_path", "total_chunks", "total_tokens", "content_hash" },
            "sections": [ { "title", "level", "children": [...] } ],
            "chunks": [ { "chunk_id", "section_path", "content", "token_estimate", ... } ]
        }
    """
    result = convert_pdf(file_path, output_format="markdown", page_range=page_range)
    markdown = result["text"]

    # Build section tree
    sections = _parse_sections(markdown)

    # Build section outline (for metadata)
    def section_outline(sec: Section) -> dict:
        outline = {"title": sec.title, "level": sec.level}
        if sec.children:
            outline["children"] = [section_outline(c) for c in sec.children]
        return outline

    section_tree = [section_outline(s) for s in sections]

    # Chunk
    chunks = _chunk_sections(sections, max_tokens=max_tokens)

    total_tokens = sum(c["token_estimate"] for c in chunks)
    content_hash = hashlib.sha256(markdown.encode()).hexdigest()[:12]

    return {
        "metadata": {
            "source_path": result["source_path"],
            "total_chunks": len(chunks),
            "total_tokens": total_tokens,
            "content_hash": content_hash,
        },
        "sections": section_tree,
        "chunks": chunks,
    }


def get_section(file_path: str, section_query: str) -> dict:
    """Convert a PDF and return only the section matching the query.

    Useful for large papers where you only need one section (e.g., "Methods").
    Matches case-insensitively against section titles and paths.
    """
    result = convert_pdf(file_path, output_format="markdown")
    markdown = result["text"]
    sections = _parse_sections(markdown)

    query_lower = section_query.lower()

    # Flatten and search
    for sec in sections:
        for item in sec.flatten():
            if (
                query_lower in item["title"].lower()
                or query_lower in item["section_path"].lower()
            ):
                return {
                    "section_path": item["section_path"],
                    "title": item["title"],
                    "level": item["level"],
                    "content": item["content"],
                    "token_estimate": _estimate_tokens(item["content"]),
                    "source_path": result["source_path"],
                }

    # List available sections for the user
    all_flat = []
    for sec in sections:
        all_flat.extend(sec.flatten())
    available = [s["section_path"] for s in all_flat]

    return {
        "error": f"Section '{section_query}' not found",
        "available_sections": available,
        "source_path": result["source_path"],
    }
