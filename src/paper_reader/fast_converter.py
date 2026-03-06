"""Fast PDF converter using PyMuPDF4LLM with optional Surya equation OCR."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

import pymupdf
import pymupdf4llm


# Math font families commonly used in LaTeX PDFs
_MATH_FONTS = frozenset({
    "cmmi", "cmsy", "cmex",          # Computer Modern math
    "lmmathitalic", "lmmathsymbols", "lmmathextension",  # Latin Modern math
    "msam", "msbm",                  # AMS math symbols
    "rsfs",                          # Ralph Smith's Formal Script
    "eufm",                          # Euler Fraktur
    "eurm", "eusm",                  # Euler
    "stmary",                        # St Mary's Road symbols
    "wasy",                          # Wasyb math
    "esint",                         # Extended integrals
    "mathpazo", "mathptmx",          # Palatino/Times math
    "newtxmath", "newpxmath",        # TX/PX math fonts
})


def _is_math_font(font_name: str) -> bool:
    """Check if a font name looks like a math font."""
    # Strip subset prefix (e.g., "ABCDEF+LMMathItalic10-Regular" -> "lmmathitalic10")
    name = font_name.lower().split("+")[-1].split("-")[0]
    # Remove trailing digits (e.g., "lmmathitalic10" -> "lmmathitalic")
    name_no_digits = re.sub(r"\d+$", "", name)
    return any(
        name.startswith(mf) or name_no_digits == mf
        for mf in _MATH_FONTS
    )


# ---------------------------------------------------------------------------
# Fast PDF conversion
# ---------------------------------------------------------------------------

def convert_pdf_fast(
    file_path: str,
    page_range: str | None = None,
) -> dict:
    """Convert a PDF to markdown using PyMuPDF4LLM (fast, no GPU needed).

    Returns same format as converter.convert_pdf().
    """
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    kwargs: dict = {}
    if page_range:
        kwargs["pages"] = _parse_page_range(page_range)

    md = pymupdf4llm.to_markdown(str(path), **kwargs)

    return {
        "text": md,
        "format": "md",
        "images": {},
        "source_path": str(path),
    }


# ---------------------------------------------------------------------------
# Equation detection
# ---------------------------------------------------------------------------

def detect_equation_regions(
    file_path: str,
    page_range: str | None = None,
    min_math_ratio: float = 0.4,
) -> list[dict]:
    """Detect equation regions by finding blocks with high math font density.

    Uses PyMuPDF font inspection to find blocks where math fonts dominate
    (not just inline variables). A block with 40%+ math font spans is
    likely a displayed equation.

    Returns:
        [{"page": int, "bbox": (x0, y0, x1, y1), "fonts": [str, ...], "math_ratio": float}, ...]
    """
    path = Path(file_path).expanduser().resolve()
    doc = pymupdf.open(str(path))

    pages_to_check = _parse_page_range(page_range) if page_range else range(len(doc))

    regions: list[dict] = []

    for page_num in pages_to_check:
        if page_num >= len(doc):
            continue
        page = doc[page_num]

        blocks = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)["blocks"]

        for block in blocks:
            if block.get("type") != 0:  # text block only
                continue

            total_spans = 0
            math_spans = 0
            math_fonts_found = set()

            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    total_spans += 1
                    font = span.get("font", "")
                    if _is_math_font(font):
                        math_spans += 1
                        math_fonts_found.add(font)

            if total_spans == 0 or not math_fonts_found:
                continue

            ratio = math_spans / total_spans
            if ratio >= min_math_ratio:
                regions.append({
                    "page": page_num,
                    "bbox": tuple(block["bbox"]),
                    "fonts": list(math_fonts_found),
                    "math_ratio": round(ratio, 2),
                })

    doc.close()
    return regions


def ocr_equations(
    file_path: str, regions: list[dict], batch_size: int = 8
) -> list[dict]:
    """OCR detected equation regions using Surya's TexifyPredictor.

    Crops each region from the PDF page, renders to image, and runs
    the Surya LaTeX OCR model.

    Returns:
        [{"page": int, "bbox": tuple, "latex": str}, ...]
    """
    if not regions:
        return []

    from PIL import Image

    path = Path(file_path).expanduser().resolve()
    doc = pymupdf.open(str(path))

    # Render equation regions as images
    images = []
    region_meta = []

    for region in regions:
        page = doc[region["page"]]
        bbox = pymupdf.Rect(region["bbox"])

        # Render at 2x resolution for better OCR
        mat = pymupdf.Matrix(2.0, 2.0)
        clip = bbox
        pix = page.get_pixmap(matrix=mat, clip=clip)

        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        images.append(img)
        region_meta.append({"page": region["page"], "bbox": region["bbox"]})

    doc.close()

    if not images:
        return []

    # Run Surya LaTeX OCR
    from surya.foundation import FoundationPredictor
    from surya.recognition import RecognitionPredictor
    from surya.common.surya.schema import TaskNames

    foundation = FoundationPredictor()
    predictor = RecognitionPredictor(foundation)

    tasks = [TaskNames.block_without_boxes] * len(images)
    bboxes = [[[0, 0, img.width, img.height]] for img in images]

    predictions = predictor(images, tasks, bboxes=bboxes)

    results = []
    for meta, pred in zip(region_meta, predictions):
        latex_text = pred.text_lines[0].text if pred.text_lines else ""
        results.append({
            "page": meta["page"],
            "bbox": meta["bbox"],
            "latex": latex_text,
        })

    return results


# ---------------------------------------------------------------------------
# Hybrid pipeline
# ---------------------------------------------------------------------------

def convert_pdf_hybrid(
    file_path: str,
    page_range: str | None = None,
    ocr_equations_flag: bool = False,
    min_eq_height: float = 20.0,
    min_eq_width: float = 100.0,
) -> dict:
    """Full hybrid pipeline: PyMuPDF4LLM text + optional Surya equation OCR.

    1. Fast text extraction with PyMuPDF4LLM
    2. (Optional) Detect equation regions via font inspection
    3. (Optional) OCR equations with Surya
    4. Append equation LaTeX as a reference section

    Args:
        ocr_equations_flag: If True, detect and OCR equations. Default False
            (adds processing time for Surya model loading + inference).
        min_eq_height: Minimum block height in points to consider as equation.
        min_eq_width: Minimum block width in points to consider as equation.

    Returns same format as converter.convert_pdf().
    """
    # Step 1: Fast text extraction
    result = convert_pdf_fast(file_path, page_range=page_range)
    md = result["text"]

    if not ocr_equations_flag:
        return result

    # Step 2: Detect equation regions (filter by size for displayed equations)
    all_regions = detect_equation_regions(file_path, page_range=page_range)
    regions = [
        r for r in all_regions
        if (r["bbox"][3] - r["bbox"][1]) >= min_eq_height
        and (r["bbox"][2] - r["bbox"][0]) >= min_eq_width
    ]

    if not regions:
        return result

    # Step 3: OCR equations
    eq_results = ocr_equations(file_path, regions)

    if not eq_results:
        return result

    # Step 4: Append equations as a reference section
    eq_section = "\n\n---\n\n## Equations (LaTeX OCR)\n\n"
    eq_section += "The following equations were detected and OCR'd from the PDF:\n\n"
    for i, eq in enumerate(eq_results, 1):
        eq_section += f"**Equation {i}** (page {eq['page'] + 1}):\n"
        eq_section += f"```latex\n{eq['latex']}\n```\n\n"

    result["text"] = md + eq_section
    result["equation_count"] = len(eq_results)

    return result


# ---------------------------------------------------------------------------
# Chunked output (compatible with store.py)
# ---------------------------------------------------------------------------

def convert_to_chunks_fast(
    file_path: str,
    max_tokens: int = 2000,
    page_range: str | None = None,
    ocr_equations_flag: bool = False,
) -> dict:
    """Convert PDF to chunks using the fast hybrid pipeline.

    Returns same format as converter.convert_to_chunks().
    """
    result = convert_pdf_hybrid(file_path, page_range=page_range, ocr_equations_flag=ocr_equations_flag)
    markdown = result["text"]

    # Reuse the section parser and chunker from converter
    from paper_reader.converter import _parse_sections, _chunk_sections

    sections = _parse_sections(markdown)

    def section_outline(sec) -> dict:
        outline = {"title": sec.title, "level": sec.level}
        if sec.children:
            outline["children"] = [section_outline(c) for c in sec.children]
        return outline

    section_tree = [section_outline(s) for s in sections]
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
