---
name: paper-reader
description: Convert PDF papers to LLM-ready markdown using marker-pdf
version: 0.0.0
user_invocable: true
---

# Paper Reader

Convert academic PDF papers into clean, structured markdown that's easy to read and reason about. Uses the `marker-pdf` library for high-quality conversion with LaTeX equation support, table extraction, and section-aware chunking.

## Available MCP Tools

This skill requires the `paper-reader` MCP server to be configured. The server provides:

- **`convert_pdf(file_path, output_format)`** - Full document conversion to markdown/HTML/JSON
- **`convert_pdf_chunks(file_path, max_tokens)`** - Chunked RAG format with section hierarchy
- **`convert_pdf_section(file_path, section)`** - Extract a single section by title

## Usage

When the user invokes `/paper-reader`, follow this workflow:

### 1. Determine the PDF source

The user may provide:
- **A file path** - use directly
- **A Zotero item key or search query** - use Zotero MCP tools to find the PDF

### 2. If using Zotero

If the user references a Zotero item (by key, title, or author search):

1. Use `zotero_search_items` to find the item
2. Use `zotero_item_metadata` to get the item details and find the attachment key
3. The PDF is stored locally at `~/Zotero/storage/{ATTACHMENT_KEY}/filename.pdf`
4. Use that path with the paper-reader MCP tools

Example flow:
```
User: "/paper-reader the Hampole 2025 paper"
1. zotero_search_items(query="Hampole 2025") -> item_key
2. zotero_item_metadata(item_key) -> find PDF attachment key
3. convert_pdf_chunks("~/Zotero/storage/{KEY}/hampole2025.pdf")
```

### 3. Convert the PDF

- For **reading/understanding**: use `convert_pdf` for full markdown
- For **large papers** or **RAG ingestion**: use `convert_pdf_chunks` with appropriate max_tokens
- For **targeted questions**: use `convert_pdf_section` to get just the relevant section

### 4. Present the result

After conversion:
- Summarize the document structure (sections found, total chunks)
- Present the content in the conversation
- For chunked output, show the section outline first, then offer to dive into specific sections

## Tips

- For papers over ~30 pages, prefer `convert_pdf_chunks` to avoid overwhelming the context
- Use `convert_pdf_section` when the user asks about a specific part (e.g., "what does the Methods section say?")
- The section hierarchy preserves subsection nesting (e.g., "Methods > Data Collection > Survey Design")
- Marker handles LaTeX equations, tables, and figures well for academic papers
