---
name: paper-reader
description: Convert PDF papers to LLM-ready markdown using marker-pdf
version: 0.0.0
user_invocable: true
---

# Paper Reader

Ingest academic PDFs into a persistent RAG store for instant semantic search. Uses a 3-tier conversion pipeline: (1) arXiv LaTeX source when available (instant, perfect), (2) PyMuPDF4LLM fast extraction (seconds), (3) marker-pdf for maximum quality.

## Available MCP Tools

This skill requires the `paper-reader` MCP server to be configured.

### RAG Store (primary workflow)

- **`ingest_paper(file_path, zotero_item_key?, title?, authors?, arxiv_id?, doi?, url?, backend?, force?)`** - Convert + embed + store. Skips if already ingested.
- **`search_papers(query, top_k?, paper_filter?, section_filter?)`** - Semantic search across all ingested papers.
- **`keyword_search_papers(query, top_k?, paper_filter?)`** - FTS5 keyword search for exact terms, names, equations.
- **`list_ingested_papers()`** - Show all papers in the store.
- **`get_paper_chunks(paper_id, section_filter?, offset?, limit?)`** - Read chunks from a specific paper.
- **`remove_paper(paper_id)`** - Delete a paper and its chunks.

### One-off Conversion (no storage)

- **`convert_pdf(file_path, output_format)`** - Full document conversion to markdown/HTML/JSON.
- **`convert_pdf_chunks(file_path, max_tokens)`** - Chunked RAG format with section hierarchy.
- **`convert_pdf_section(file_path, section)`** - Extract a single section by title.

## Usage

When the user invokes `/paper-reader`, follow this workflow:

### 1. Determine the PDF source

The user may provide:
- **A file path** - use directly
- **A Zotero item key or search query** - use Zotero MCP tools to find the PDF

### 2. If using Zotero (IMPORTANT: always extract DOI/URL)

If the user references a Zotero item:

1. Use `zotero_search_items` to find the item
2. Use `zotero_get_item_metadata` to get details, **including DOI and URL**
3. Use `zotero_get_item_children` to find the PDF attachment key
4. The PDF is at `~/Zotero/storage/{ATTACHMENT_KEY}/filename.pdf`
5. **Always pass DOI and URL** to `ingest_paper` so it can try arXiv source first

Example flow:
```
User: "/paper-reader the Hampole 2025 paper"
1. zotero_search_items(query="Hampole 2025") -> item_key, DOI="10.3386/w31161", URL="..."
2. zotero_get_item_children(item_key) -> attachment_key, filename
3. ingest_paper(
     file_path="~/Zotero/storage/{KEY}/hampole.pdf",
     zotero_item_key="GISBZ42S",
     doi="10.3386/w31161",
     url="https://arxiv.org/abs/...",   # if available
     title="...", authors="..."
   )
4. Report: "Ingested 45 chunks via arXiv LaTeX source (2.3s). Ready to search."
```

### 3. Conversion backends

The `backend` parameter controls how the PDF is converted:

- **`"auto"` (default)**: Tries arXiv LaTeX source first (if DOI/URL/arxiv_id provided), falls back to fast PyMuPDF extraction. Best choice for most papers.
- **`"arxiv"`**: arXiv LaTeX source only. Fails if paper isn't on arXiv.
- **`"fast"`**: PyMuPDF4LLM. Seconds for any paper, no GPU needed.
- **`"marker"`**: marker-pdf. Slow (~20s/page) but highest quality for complex layouts.

### 4. Present results

After ingestion:
- Report which backend was used, chunk count, and timing
- Offer to search or browse sections

After search:
- Show the top results with section paths and scores
- Offer to get more context from the same paper/section

## Tips

- **Always pass DOI/URL from Zotero metadata.** This enables automatic arXiv source detection, giving you perfect LaTeX with equations preserved.
- Second calls to `ingest_paper` skip automatically (content hash check).
- Use `paper_filter` in search to scope results to a single paper.
- Use `section_filter` to narrow results to specific parts (e.g., "Methods", "Results").
- For arXiv papers, the chunks contain raw LaTeX math (great for semantic search on equations).
