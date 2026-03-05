"""MCP server exposing PDF conversion tools via FastMCP."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "paper-reader",
    instructions="Convert academic PDFs to LLM-ready markdown with section-aware chunking",
)


@mcp.tool()
def convert_pdf(
    file_path: str,
    output_format: str = "markdown",
    page_range: str = "",
) -> str:
    """Convert a PDF file to markdown, HTML, or JSON.

    Args:
        file_path: Absolute path to the PDF file.
        output_format: Output format - "markdown" (default), "html", or "json".
        page_range: Optional page range (0-indexed), e.g. "0-4" for first 5 pages.
            Leave empty for all pages.

    Returns:
        The converted document text.
    """
    from paper_reader.converter import convert_pdf as _convert

    result = _convert(
        file_path,
        output_format=output_format,
        page_range=page_range or None,
    )
    return result["text"]


@mcp.tool()
def convert_pdf_chunks(
    file_path: str,
    max_tokens: int = 2000,
    page_range: str = "",
) -> str:
    """Convert a PDF to chunked RAG format with section hierarchy.

    Parses the PDF into a section/subsection tree, then splits into
    token-bounded chunks preserving section context. Each chunk includes
    its section path (e.g. "Methods > Data Collection") and token estimate.

    Args:
        file_path: Absolute path to the PDF file.
        max_tokens: Maximum tokens per chunk (approximate). Default 2000.
        page_range: Optional page range (0-indexed), e.g. "0-9" for first 10 pages.
            Leave empty for all pages.

    Returns:
        JSON with metadata, section outline, and chunks array.
    """
    from paper_reader.converter import convert_to_chunks

    result = convert_to_chunks(
        file_path, max_tokens=max_tokens, page_range=page_range or None,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def convert_pdf_section(file_path: str, section: str) -> str:
    """Convert a PDF and return only the matching section.

    Useful for large papers where you need a specific part (e.g. "Abstract",
    "Methods", "Results"). Matches case-insensitively against section titles.

    Args:
        file_path: Absolute path to the PDF file.
        section: Section title to search for (case-insensitive partial match).

    Returns:
        The matching section content, or a list of available sections if not found.
    """
    from paper_reader.converter import get_section

    result = get_section(file_path, section)
    return json.dumps(result, indent=2)


def main():
    """Entry point for paper-reader-mcp command."""
    mcp.run()


if __name__ == "__main__":
    main()
