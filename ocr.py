"""
ocr.py — OCR and text extraction.
All four input types: image, PDF, plain text, markdown.
Two OCR engines: Gemini (cloud) and Tesseract (local).
Gemini is more accurate and preserves formatting better, but has a daily quota.
Tesseract is free and runs locally, but can be less accurate. 
"""

import logging
import re
import uuid
from pathlib import Path

import pytesseract
import pdfplumber
from PIL import Image
from pdf2image import convert_from_path
from google import genai
from google.genai import types as genai_types

import config

log = logging.getLogger(__name__)

# ── Gemini client (lazy-init, shared across calls) ────────────────────────────

_client = None

def _gemini():
    global _client
    if _client is None:
        _client = genai.Client(
            http_options=genai_types.HttpOptions(
                client_args={'timeout': config.API_TIMEOUT_SECONDS}
            )
        )
    return _client


# ── Images ────────────────────────────────────────────────────────────────────

def extract_from_image(
    path: Path,
    engine: str = None,
    prompt_key: str = None,
) -> str:
    """
    Extract text from a single JPG/PNG image.

    Args:
        path:       Path to the image file.
        engine:     'gemini' | 'tesseract'. Defaults to config.DEFAULT_OCR_ENGINE.
        prompt_key: Key into config.OCR_PROMPTS. Defaults to config.DEFAULT_OCR_PROMPT.
                    Controls what instruction Gemini receives — match this to your content type.

    Returns plain text string.
    """
    engine     = engine     or config.DEFAULT_OCR_ENGINE
    prompt_key = prompt_key or config.DEFAULT_OCR_PROMPT

    if engine not in ("gemini", "tesseract"):
        raise ValueError(
            f"Unknown OCR engine: {engine!r}. Valid options: 'gemini', 'tesseract'."
        )

    # PIL Image used as context manager — file handle always closed, even on exception
    with Image.open(path) as img:
        if engine == "tesseract":
            return pytesseract.image_to_string(img).strip()

        prompt   = config.OCR_PROMPTS.get(prompt_key, config.OCR_PROMPTS["default"])
        response = _gemini().models.generate_content(
            model    = config.GEMINI_OCR_MODEL,
            contents = [img, prompt],
        )
        return (response.text or "").strip()


# ── PDFs ──────────────────────────────────────────────────────────────────────

def _is_text_native(path: Path) -> bool:
    """Return True if the PDF has selectable text (not a scanned image PDF)."""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:3]:
            if page.extract_text():
                return True
    return False


def get_pdf_page_count(path: Path) -> int:
    """Return total number of pages in a PDF."""
    with pdfplumber.open(path) as pdf:
        return len(pdf.pages)


def extract_from_pdf(path: Path, engine: str = None) -> list[str] | None:
    """
    Text-native PDFs: returns list of page text strings (fast, no API calls).
    Scanned PDFs:     returns None — caller should use extract_scanned_page() per page.

    The None return is a deliberate signal, not an error.
    Caller pattern:
        pages = ocr.extract_from_pdf(path)
        if pages is None:
            # scanned — use per-page path
    """
    if _is_text_native(path):
        log.info("[ocr] PDF is text-native — using pdfplumber (no API calls)")
        with pdfplumber.open(path) as pdf:
            return [(page.extract_text() or "").strip() for page in pdf.pages]
    return None


def extract_scanned_page(path: Path, page_index: int, engine: str = None) -> str:
    """
    Extract text from one page of a scanned PDF (0-indexed).

    Uses a UUID-based temp filename to prevent race conditions when
    multiple ingests run concurrently. Temp file is always cleaned up,
    even if extract_from_image() raises an exception.
    """
    engine = engine or config.DEFAULT_OCR_ENGINE

    images = convert_from_path(
        path,
        first_page = page_index + 1,
        last_page  = page_index + 1,
    )
    if not images:
        return ""

    # UUID in filename prevents concurrent ingests from overwriting each other
    tmp = Path(f"/tmp/_reader_{uuid.uuid4().hex}_p{page_index}.jpg")
    images[0].save(tmp, "JPEG")

    try:
        return extract_from_image(tmp, engine=engine)
    finally:
        tmp.unlink(missing_ok=True)   # always cleans up, even on exception


# ── Plain text / Markdown ─────────────────────────────────────────────────────

_MD_SYNTAX = re.compile(
    r"(\*{1,3}|_{1,3})(.*?)\1"
    r"|^#{1,6}\s+"
    r"|`{1,3}.*?`{1,3}"
    r"|\[([^\]]+)\]\([^)]+\)",
    re.MULTILINE | re.DOTALL
)


def extract_from_text(path: Path) -> list[str]:
    """
    Read a .txt or .md file.
    Splits into PAGE_SIZE-character chunks (configured in config.py).
    """
    raw = path.read_text(encoding="utf-8", errors="replace")

    if path.suffix.lower() == ".md":
        raw = _MD_SYNTAX.sub(r"\2\3", raw)

    paragraphs = raw.split("\n\n")
    pages, current = [], ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) > config.PAGE_SIZE and current:
            pages.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        pages.append(current.strip())

    return pages
