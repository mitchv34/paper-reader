"""MCP server exposing PDF conversion tools via FastMCP."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "paper-reader",
    instructions="Convert academic PDFs to LLM-ready markdown with section-aware chunking",
)

# Lazy singleton for the persistent store
_store = None


def _get_store():
    global _store
    if _store is None:
        from paper_reader.store import PaperStore
        _store = PaperStore()
    return _store


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


# ------------------------------------------------------------------
# RAG Store tools
# ------------------------------------------------------------------


@mcp.tool()
def ingest_paper(
    file_path: str,
    zotero_item_key: str = "",
    title: str = "",
    authors: str = "",
    max_tokens: int = 2000,
    page_range: str = "",
    force: bool = False,
) -> str:
    """Convert a PDF, compute embeddings, and store for instant search.

    Skips if the paper was already ingested and content is unchanged.
    Use force=True to re-ingest even if unchanged.

    Args:
        file_path: Absolute path to the PDF file.
        zotero_item_key: Optional Zotero item key to link this paper.
        title: Optional paper title (auto-extracted from PDF if omitted).
        authors: Optional authors string.
        max_tokens: Maximum tokens per chunk (default 2000).
        page_range: Optional page range (0-indexed), e.g. "0-4" for first 5 pages.
            Leave empty for all pages.
        force: Re-ingest even if content hash is unchanged.

    Returns:
        JSON with ingestion status, paper_id, chunk count, and timing.
    """
    store = _get_store()
    result = store.ingest(
        file_path=file_path,
        zotero_item_key=zotero_item_key,
        title=title,
        authors=authors,
        max_tokens=max_tokens,
        page_range=page_range or None,
        force=force,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def search_papers(
    query: str,
    top_k: int = 5,
    paper_filter: str = "",
    section_filter: str = "",
) -> str:
    """Search across all ingested papers by semantic similarity.

    Returns the most relevant chunks with cosine similarity scores.

    Args:
        query: Natural language search query.
        top_k: Number of results to return (default 5).
        paper_filter: Filter by paper title (partial match) or Zotero item key.
        section_filter: Filter by section path (partial match, e.g. "Methods").

    Returns:
        JSON array of matching chunks with scores, paper info, and content.
    """
    store = _get_store()
    results = store.semantic_search(
        query=query,
        top_k=top_k,
        paper_filter=paper_filter,
        section_filter=section_filter,
    )
    return json.dumps(results, indent=2)


@mcp.tool()
def keyword_search_papers(
    query: str,
    top_k: int = 10,
    paper_filter: str = "",
) -> str:
    """Full-text keyword search across ingested papers using BM25.

    Best for exact terms, names, equations, or specific phrases.

    Args:
        query: Keyword search query (FTS5 syntax supported).
        top_k: Number of results to return (default 10).
        paper_filter: Filter by paper title (partial match) or Zotero item key.

    Returns:
        JSON array of matching chunks with BM25 scores and paper info.
    """
    store = _get_store()
    results = store.keyword_search(
        query=query,
        top_k=top_k,
        paper_filter=paper_filter,
    )
    return json.dumps(results, indent=2)


@mcp.tool()
def list_ingested_papers() -> str:
    """List all papers in the persistent store.

    Returns:
        JSON array of papers with titles, authors, chunk counts, and ingestion dates.
    """
    store = _get_store()
    papers = store.list_papers()
    return json.dumps(papers, indent=2)


@mcp.tool()
def get_paper_chunks(
    paper_id: str,
    section_filter: str = "",
    offset: int = 0,
    limit: int = 10,
) -> str:
    """Retrieve chunks from a specific ingested paper.

    Use after search to read more context from a paper, or to browse
    a paper's content section by section.

    Args:
        paper_id: Paper ID (integer) or Zotero item key.
        section_filter: Filter by section path (partial match, e.g. "Results").
        offset: Skip this many chunks (for pagination).
        limit: Return at most this many chunks (default 10).

    Returns:
        JSON with paper metadata, section outline, and chunks array.
    """
    store = _get_store()
    result = store.get_chunks(
        paper_id=paper_id,
        section_filter=section_filter,
        offset=offset,
        limit=limit,
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def remove_paper(paper_id: str) -> str:
    """Remove a paper and all its chunks from the store.

    Args:
        paper_id: Paper ID (integer) or Zotero item key.

    Returns:
        JSON with removal status.
    """
    store = _get_store()
    result = store.remove_paper(paper_id=paper_id)
    return json.dumps(result, indent=2)


def main():
    """Entry point for paper-reader-mcp command."""
    mcp.run()


if __name__ == "__main__":
    main()
