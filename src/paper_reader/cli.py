"""Standalone CLI for paper-reader."""

from __future__ import annotations

import json
import sys

import click


@click.group()
def main():
    """Convert academic PDFs to LLM-ready markdown."""


@main.command()
@click.argument("file_path", type=click.Path(exists=True))
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["markdown", "html", "json"]),
    default="markdown",
    help="Output format (default: markdown).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Write output to file instead of stdout.",
)
@click.option(
    "--pages",
    type=str,
    default=None,
    help="Page range to convert (0-indexed), e.g. '0-4' for first 5 pages.",
)
def convert(file_path: str, output_format: str, output: str | None, pages: str | None):
    """Convert a PDF to markdown, HTML, or JSON."""
    from paper_reader.converter import convert_pdf

    result = convert_pdf(file_path, output_format=output_format, page_range=pages)
    text = result["text"]

    if output:
        with open(output, "w") as f:
            f.write(text)
        click.echo(f"Written to {output}", err=True)
    else:
        click.echo(text)


@main.command()
@click.argument("file_path", type=click.Path(exists=True))
@click.option(
    "--max-tokens",
    type=int,
    default=2000,
    help="Max tokens per chunk (default: 2000).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Write JSON output to file.",
)
@click.option(
    "--pages",
    type=str,
    default=None,
    help="Page range to convert (0-indexed), e.g. '0-9' for first 10 pages.",
)
def chunks(file_path: str, max_tokens: int, output: str | None, pages: str | None):
    """Convert a PDF to chunked RAG format with section hierarchy."""
    from paper_reader.converter import convert_to_chunks

    result = convert_to_chunks(file_path, max_tokens=max_tokens, page_range=pages)
    text = json.dumps(result, indent=2)

    if output:
        with open(output, "w") as f:
            f.write(text)
        click.echo(f"Written to {output}", err=True)
    else:
        click.echo(text)


@main.command()
@click.argument("file_path", type=click.Path(exists=True))
@click.argument("section_query")
def section(file_path: str, section_query: str):
    """Extract a single section from a PDF by title.

    Example: paper-reader section paper.pdf "Methods"
    """
    from paper_reader.converter import get_section

    result = get_section(file_path, section_query)

    if "error" in result:
        click.echo(f"Not found: {result['error']}", err=True)
        click.echo("Available sections:", err=True)
        for s in result.get("available_sections", []):
            click.echo(f"  - {s}", err=True)
        sys.exit(1)
    else:
        click.echo(result["content"])


if __name__ == "__main__":
    main()
