# paper-reader

Convert academic PDFs to LLM-ready markdown with section-aware chunking. Wraps [marker-pdf](https://github.com/datalab-to/marker) as an MCP server and Claude Code skill.

## Features

- High-quality PDF to markdown (equations, tables, figures)
- Section/subsection hierarchy parsing
- Token-bounded chunking for RAG pipelines
- Single-section extraction for targeted queries
- Composes with Zotero MCP for library integration

## Install

```bash
pip install git+https://github.com/mitchv34/paper-reader.git
```

Or for development:

```bash
git clone https://github.com/mitchv34/paper-reader.git
cd paper-reader
pip install -e .
```

## MCP Server Setup

### Claude Code

Add to your project's `.mcp.json` or `~/.claude.json`:

```json
{
  "mcpServers": {
    "paper-reader": {
      "command": "paper-reader-mcp",
      "args": []
    }
  }
}
```

### Gemini CLI

Add to `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "paper-reader": {
      "command": "paper-reader-mcp"
    }
  }
}
```

### Cursor / Windsurf / other MCP clients

Point to the `paper-reader-mcp` command as an stdio MCP server.

## Claude Code Skill

To install the skill (enables the `/paper-reader` command):

```bash
cp -r skill/ ~/.claude/skills/paper-reader/
# or symlink:
ln -s $(pwd)/skill ~/.claude/skills/paper-reader
```

The skill teaches the agent to compose paper-reader with Zotero MCP. You can say things like:
- `/paper-reader ~/Downloads/paper.pdf`
- `/paper-reader the Hampole 2025 paper` (looks up in Zotero first)

## CLI Usage

```bash
# Full markdown conversion
paper-reader convert paper.pdf

# Chunked RAG format
paper-reader chunks paper.pdf --max-tokens 2000

# Extract a single section
paper-reader section paper.pdf "Methods"

# Write output to file
paper-reader convert paper.pdf -o paper.md
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `convert_pdf` | Full document conversion (markdown, HTML, or JSON) |
| `convert_pdf_chunks` | Chunked RAG output with section hierarchy |
| `convert_pdf_section` | Extract a single section by title |

## Composing with Zotero

paper-reader is standalone. It works with file paths. To use with your Zotero library, let the AI agent handle the composition:

1. Zotero MCP finds the paper and provides the PDF path
2. paper-reader converts the PDF to markdown

The Claude Code skill automates this workflow. With both MCP servers configured, just reference a paper by title or author.

## Requirements

- Python 3.10+
- PyTorch (installed automatically with marker-pdf)
- ~3.5 GB disk for marker models (downloaded on first use)
