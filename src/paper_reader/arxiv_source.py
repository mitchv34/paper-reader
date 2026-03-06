"""Fetch and parse LaTeX source from arXiv for perfect-quality ingestion."""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import tarfile
import time
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ---------------------------------------------------------------------------
# arXiv ID extraction
# ---------------------------------------------------------------------------

_ARXIV_DOI_RE = re.compile(r"10\.48550/arXiv\.(\d{4}\.\d{4,5}(?:v\d+)?)", re.I)
_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf|e-print)/(\d{4}\.\d{4,5}(?:v\d+)?)", re.I)
_ARXIV_OLD_RE = re.compile(r"arxiv\.org/(?:abs|pdf|e-print)/([\w-]+/\d{7}(?:v\d+)?)", re.I)
_ARXIV_BARE_RE = re.compile(r"^(\d{4}\.\d{4,5}(?:v\d+)?)$")


def extract_arxiv_id(
    doi: str = "", url: str = "", arxiv_id: str = ""
) -> Optional[str]:
    """Try to extract an arXiv ID from various identifiers.

    Checks (in order): explicit arxiv_id, DOI pattern, URL pattern.
    Returns the arXiv ID string or None.
    """
    # Direct ID
    if arxiv_id:
        m = _ARXIV_BARE_RE.match(arxiv_id.strip())
        if m:
            return m.group(1)
        # Maybe they passed a full URL or DOI as arxiv_id
        for regex in (_ARXIV_DOI_RE, _ARXIV_URL_RE, _ARXIV_OLD_RE):
            m = regex.search(arxiv_id)
            if m:
                return m.group(1)

    # DOI
    if doi:
        m = _ARXIV_DOI_RE.search(doi)
        if m:
            return m.group(1)

    # URL
    if url:
        for regex in (_ARXIV_URL_RE, _ARXIV_OLD_RE):
            m = regex.search(url)
            if m:
                return m.group(1)

    return None


# ---------------------------------------------------------------------------
# Source download
# ---------------------------------------------------------------------------

def _default_cache_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return Path(xdg) / "paper-reader" / "arxiv_cache"


def fetch_arxiv_source(
    arxiv_id: str,
    cache_dir: Optional[Path] = None,
) -> Optional[dict]:
    """Download and extract LaTeX source from arXiv.

    Returns:
        {
            "arxiv_id": str,
            "tex_content": str,  # merged main .tex with resolved includes
            "files": list[str],  # all files in the archive
            "main_file": str,    # name of the main .tex file
        }
        or None if source is not available.
    """
    cache = cache_dir or _default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)

    # Strip version suffix for caching
    base_id = re.sub(r"v\d+$", "", arxiv_id)
    paper_dir = cache / base_id.replace("/", "_")

    # Check cache
    if paper_dir.exists() and any(paper_dir.glob("*.tex")):
        return _load_from_cache(arxiv_id, paper_dir)

    # Download
    url = f"https://arxiv.org/e-print/{arxiv_id}"
    req = Request(url, headers={"User-Agent": "paper-reader/0.1 (academic tool)"})

    try:
        with urlopen(req, timeout=30) as resp:
            data = resp.read()
    except (HTTPError, URLError):
        return None

    # Extract archive
    paper_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Try as tar.gz first (multi-file submissions)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            # Security: only extract safe paths
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    continue
                tar.extract(member, path=paper_dir)
    except tarfile.TarError:
        # Single-file submission (just gzipped .tex)
        try:
            content = gzip.decompress(data)
            (paper_dir / "main.tex").write_bytes(content)
        except gzip.BadGzipFile:
            # Raw file (rare)
            (paper_dir / "main.tex").write_bytes(data)

    return _load_from_cache(arxiv_id, paper_dir)


def _load_from_cache(arxiv_id: str, paper_dir: Path) -> Optional[dict]:
    """Load and merge .tex files from cache directory."""
    tex_files = list(paper_dir.rglob("*.tex"))
    if not tex_files:
        return None

    all_files = [str(f.relative_to(paper_dir)) for f in paper_dir.rglob("*") if f.is_file()]

    # Find main .tex file
    main_file = _find_main_tex(tex_files)
    if main_file is None:
        return None

    # Read and resolve includes
    tex_content = _resolve_includes(main_file, paper_dir)

    return {
        "arxiv_id": arxiv_id,
        "tex_content": tex_content,
        "files": all_files,
        "main_file": str(main_file.relative_to(paper_dir)),
    }


def _find_main_tex(tex_files: list[Path]) -> Optional[Path]:
    """Find the main .tex file (the one with \\documentclass)."""
    for f in tex_files:
        try:
            content = f.read_text(errors="replace")
            if r"\documentclass" in content:
                return f
        except OSError:
            continue

    # Fallback: prefer ms.tex, main.tex, paper.tex
    for name in ("ms.tex", "main.tex", "paper.tex"):
        for f in tex_files:
            if f.name.lower() == name:
                return f

    # Last resort: first .tex file
    return tex_files[0] if tex_files else None


def _resolve_includes(main_file: Path, base_dir: Path) -> str:
    """Read main .tex and resolve \\input{} and \\include{} directives."""
    content = main_file.read_text(errors="replace")

    def replacer(match):
        cmd = match.group(1)  # "input" or "include"
        filename = match.group(2).strip()
        # Add .tex extension if missing
        if not filename.endswith(".tex"):
            filename += ".tex"

        inc_path = base_dir / filename
        if not inc_path.exists():
            # Try relative to main file
            inc_path = main_file.parent / filename
        if not inc_path.exists():
            return match.group(0)  # leave as-is if not found

        inc_content = inc_path.read_text(errors="replace")
        if cmd == "include":
            inc_content = "\n\\clearpage\n" + inc_content + "\n\\clearpage\n"
        return inc_content

    # Resolve includes (up to 3 levels deep)
    for _ in range(3):
        new_content = re.sub(
            r"\\(input|include)\{([^}]+)\}", replacer, content
        )
        if new_content == content:
            break
        content = new_content

    return content


# ---------------------------------------------------------------------------
# LaTeX parsing
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(
    r"\\(section|subsection|subsubsection|paragraph)\*?\{([^}]+)\}",
    re.IGNORECASE,
)

_LEVEL_MAP = {
    "section": 1,
    "subsection": 2,
    "subsubsection": 3,
    "paragraph": 4,
}


def _strip_preamble(tex: str) -> str:
    """Extract content between \\begin{document} and \\end{document}."""
    begin = re.search(r"\\begin\{document\}", tex)
    end = re.search(r"\\end\{document\}", tex)
    if begin:
        tex = tex[begin.end():]
    if end:
        tex = tex[:end.start()]
    return tex.strip()


def _clean_latex(text: str) -> str:
    """Light cleanup of LaTeX for readability while preserving math."""
    # Remove comments
    text = re.sub(r"(?<!\\)%.*$", "", text, flags=re.MULTILINE)
    # Remove common formatting commands but preserve content
    for cmd in ("textbf", "textit", "emph", "texttt", "underline"):
        text = re.sub(rf"\\{cmd}\{{([^}}]+)\}}", r"\1", text)
    # Remove labels and refs (keep ref text if any)
    text = re.sub(r"\\label\{[^}]+\}", "", text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _estimate_tokens(text: str) -> int:
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


def parse_latex_to_chunks(tex_content: str, max_tokens: int = 2000) -> dict:
    """Parse LaTeX into sections/chunks compatible with convert_to_chunks() format.

    Returns:
        {
            "metadata": { "source_path", "total_chunks", "total_tokens", "content_hash" },
            "sections": [ { "title", "level", ... } ],
            "chunks": [ { "chunk_id", "section_path", "content", "token_estimate", ... } ]
        }
    """
    body = _strip_preamble(tex_content)
    body = _clean_latex(body)

    # Find all section markers
    markers = list(_SECTION_RE.finditer(body))

    # Build flat sections
    sections_flat: list[dict] = []

    # Preamble text (before first section)
    if markers:
        preamble = body[:markers[0].start()].strip()
        if preamble:
            sections_flat.append({
                "title": "Preamble",
                "level": 0,
                "content": preamble,
            })

        for i, m in enumerate(markers):
            cmd = m.group(1).lower()
            title = m.group(2).strip()
            level = _LEVEL_MAP.get(cmd, 1)

            end = markers[i + 1].start() if i + 1 < len(markers) else len(body)
            content = body[m.end():end].strip()

            sections_flat.append({
                "title": title,
                "level": level,
                "content": content,
            })
    else:
        # No sections found, treat as single section
        sections_flat.append({
            "title": "Document",
            "level": 1,
            "content": body,
        })

    # Build section tree (for outline)
    section_tree = []
    stack: list[dict] = []
    for sec in sections_flat:
        node = {"title": sec["title"], "level": sec["level"]}
        while stack and stack[-1]["level"] >= sec["level"]:
            stack.pop()
        if stack:
            stack[-1].setdefault("children", []).append(node)
        else:
            section_tree.append(node)
        stack.append(node)

    # Build section paths and chunk
    chunks: list[dict] = []
    chunk_id = 0
    path_stack: list[tuple[int, str]] = []  # (level, title)

    for sec in sections_flat:
        # Update path stack
        while path_stack and path_stack[-1][0] >= sec["level"]:
            path_stack.pop()
        path_stack.append((sec["level"], sec["title"]))
        section_path = " > ".join(t for _, t in path_stack)

        if not sec["content"].strip():
            continue

        pieces = _split_text(sec["content"], max_tokens)
        for i, piece in enumerate(pieces):
            chunks.append({
                "chunk_id": chunk_id,
                "section_path": section_path,
                "section_title": sec["title"],
                "section_level": sec["level"],
                "part": i + 1 if len(pieces) > 1 else None,
                "total_parts": len(pieces) if len(pieces) > 1 else None,
                "content": piece,
                "token_estimate": _estimate_tokens(piece),
            })
            chunk_id += 1

    total_tokens = sum(c["token_estimate"] for c in chunks)
    content_hash = hashlib.sha256(tex_content.encode()).hexdigest()[:12]

    return {
        "metadata": {
            "source_path": "arxiv",
            "total_chunks": len(chunks),
            "total_tokens": total_tokens,
            "content_hash": content_hash,
        },
        "sections": section_tree,
        "chunks": chunks,
    }
