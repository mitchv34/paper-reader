---
name: paper-reader
description: Convert PDF papers to LLM-ready markdown using marker-pdf
version: 0.0.0
user_invocable: true
---

# Paper Reader

Ingest academic PDFs into a persistent RAG store for instant semantic search. Converts papers once using `marker-pdf`, computes embeddings, and stores chunks in SQLite for fast retrieval. Also supports one-off conversion without ingestion.

## Available MCP Tools

This skill requires the `paper-reader` MCP server to be configured.

### RAG Store (primary workflow)

- **`ingest_paper(file_path, zotero_item_key?, title?, authors?, force?)`** - Convert + embed + store. Skips if already ingested.
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

### 2. If using Zotero

If the user references a Zotero item (by key, title, or author search):

1. Use `zotero_search_items` to find the item
2. Use `zotero_get_item_metadata` to get details
3. Use `zotero_get_item_children` to find the PDF attachment key
4. The PDF is stored locally at `~/Zotero/storage/{ATTACHMENT_KEY}/filename.pdf`
5. Ingest with both the file path and the zotero_item_key for linking

Example flow:
```
User: "/paper-reader the Hampole 2025 paper"
1. zotero_search_items(query="Hampole 2025") -> item_key "ABC12345"
2. zotero_get_item_children(item_key="ABC12345") -> attachment_key "XYZ98765", filename
3. ingest_paper(
     file_path="~/Zotero/storage/XYZ98765/hampole2025.pdf",
     zotero_item_key="ABC12345",
     title="...", authors="..."
   )
4. Report: "Ingested 45 chunks. Ready to search."
```

### 3. Choose the right action

- **First time with a paper**: Use `ingest_paper` to store it permanently. This takes ~30-60s but only happens once.
- **Searching across papers**: Use `search_papers` for semantic search (instant) or `keyword_search_papers` for exact terms.
- **Reading a specific section**: Use `get_paper_chunks` with a section_filter after finding the paper_id.
- **Quick one-off read**: Use `convert_pdf` or `convert_pdf_section` if the user just wants to glance at something without storing it.

### 4. Present results

After ingestion:
- Report chunk count and timing
- Offer to search or browse sections

After search:
- Show the top results with section paths and scores
- Offer to get more context from the same paper/section

## Tips

- Always pass `zotero_item_key` when ingesting from Zotero, so the paper is linked for future lookups.
- Second calls to `ingest_paper` with the same file skip automatically (content hash check).
- Use `paper_filter` in search to scope results to a single paper by title or Zotero key.
- Use `section_filter` to narrow results to specific parts (e.g., "Methods", "Results").
- The section hierarchy preserves subsection nesting (e.g., "Methods > Data Collection > Survey Design").
