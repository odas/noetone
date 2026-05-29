"""
pipeline.py — Core ingestion pipeline and resilience layer.

WHY THIS MODULE EXISTS:
  Before: ingest() lived in watcher.py (a filesystem-monitor module).
          app.py needed late imports to avoid a circular dependency.
  Now:    watcher.py and app.py both import cleanly from pipeline.
          No cycles. No late imports. No surprises.

OWNS:
  • ingest()             — file → OCR → SQLite, the full pipeline
  • classify_api_error() — single point of truth for Gemini error classification
  • CircuitBreaker       — consecutive-failure counter, opens on quota exhaustion
  • _ocr_with_retry()    — retry / backoff / circuit logic for all OCR calls

DOES NOT OWN:
  • Filesystem watching  → watcher.py
  • HTTP routing         → app.py
  • Database schema      → db.py
  • OCR mechanics        → ocr.py
"""

import logging
import re
import time
import uuid
from pathlib import Path

from google.genai import errors as genai_errors

import config
import db
import ocr

log = logging.getLogger(__name__)

SUPPORTED = {".jpg", ".jpeg", ".png", ".pdf", ".txt", ".md"}


# ── Error classification ──────────────────────────────────────────────────────
#
# THIS IS THE ONLY PLACE that decides what kind of API error occurred.
# When Gemini changes its error response format (and it will), update
# classify_api_error() here. Nothing else needs to change.

class QuotaExhaustedError(Exception):
    """Daily API quota confirmed exhausted. Do not retry until tomorrow."""

class TransientAPIError(Exception):
    """Short-lived failure (network blip, brief unavailability). Safe to retry."""


def classify_api_error(err: Exception) -> str:
    """
    Classifies a Gemini API exception.

    Returns:
        'rpm'       — rate-per-minute hit; Gemini gives a short retry delay in seconds
        'quota'     — daily limit exhausted; do not retry today
        'transient' — network/availability issue; retry with backoff
        'unknown'   — non-quota error; fail fast, don't retry

    UPDATE THIS FUNCTION when Gemini changes its error response format.
    It is the single point of truth. All retry and circuit-breaker logic
    reads from this function's output — nowhere else.

    Detection strategy (in priority order):
        1. APIError.code — HTTP status code, most reliable.
           429 → quota/rpm. 503/502 → transient.
        2. Structured metadata — stable API contract.
           Check details for google.rpc.QuotaFailure type.
        3. String matching — fragile fallback only.
    """
    # ── 1. APIError.code (most reliable) ─────────────────────────────────────
    if isinstance(err, genai_errors.APIError):
        code = getattr(err, 'code', None)
        if code == 429:
            # Distinguish RPM (short retry window) from daily quota
            msg = str(err).lower()
            if (re.search(r"retry.{0,20}after.{0,10}\d+\s*s", msg) or
                    re.search(r"retrydelay.{0,10}\d+s", msg)):
                return "rpm"
            return "quota"
        if 500 <= code < 600:
            return "transient"

    # ── 2. Structured metadata ────────────────────────────────────────────────
    _details = getattr(err, 'details', None)
    if isinstance(_details, list):
        for detail in _details:
            if not isinstance(detail, dict):
                continue
            if detail.get('@type') == 'type.googleapis.com/google.rpc.QuotaFailure':
                for violation in detail.get('violations', []):
                    metric = violation.get('quotaMetric', '').lower()
                    if 'day' in metric or 'daily' in metric:
                        return 'quota'
                return 'rpm'

    # ── 3. String matching fallback ───────────────────────────────────────────
    msg = str(err).lower()

    if "429" in msg or "resource_exhausted" in msg:
        if (re.search(r"retry.{0,20}after.{0,10}\d+\s*s", msg) or
                re.search(r"retrydelay.{0,10}\d+s", msg)):
            return "rpm"
        return "quota"

    if any(t in msg for t in ["503", "502", "unavailable", "timeout", "connection", "timed out"]):
        return "transient"

    return "unknown"


def _retry_delay_from_error(err: Exception) -> int:
    """
    Extract suggested retry delay from a Gemini RPM error.

    Strategy:
    1. Structured read from APIError.details — stable API contract.
       Gemini RPM errors include a google.rpc.RetryInfo detail with
       retryDelay as a protobuf Duration string e.g. "15s" or "30.500s".
       Path: err.details['error']['details'][n]['retryDelay']
    2. Regex fallback — 1-3 digit numbers only, rejects timestamps.
    3. Hard cap at 60s regardless of source.
    """
    MAX_SENSIBLE_DELAY = 60
    DEFAULT_DELAY      = 30

    # ── 1. Structured (preferred) ─────────────────────────────────────────────
    if isinstance(err, genai_errors.APIError):
        try:
            details = getattr(err, 'details', {}) or {}
            if not isinstance(details, dict):
                details = {}
            error_dict = details.get('error', details)
            for detail in error_dict.get('details', []):
                retry_delay = detail.get('retryDelay', '')
                if retry_delay:
                    # Duration string: "15s", "30.500s" — strip 's', parse float
                    delay = int(float(str(retry_delay).rstrip('s'))) + 2
                    return min(delay, MAX_SENSIBLE_DELAY)
        except (AttributeError, TypeError, ValueError, KeyError):
            pass  # fall through to regex

    # ── 2. Regex fallback ─────────────────────────────────────────────────────
    # 1-3 digits only — rejects timestamps like 68236665s
    match = re.search(r'\b(\d{1,3})s\b', str(err).lower())
    if match:
        delay = int(match.group(1)) + 2
        return min(delay, MAX_SENSIBLE_DELAY)

    return DEFAULT_DELAY


# ── Circuit breaker ───────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Consecutive-failure counter. Opens after `threshold` failures.
    Once open, calls are rejected immediately — stops hammering a dead API.
    Reset automatically on the next successful call.

    Usage:
        circuit = CircuitBreaker(name="gemini_ocr")
        if circuit.is_open:
            raise QuotaExhaustedError(...)
        try:
            result = call_api()
            circuit.record_success()
        except QuotaExhaustedError:
            circuit.record_failure()
            raise
    """
    def __init__(self, name: str, threshold: int = None):
        self.name      = name
        self.threshold = threshold or config.CIRCUIT_BREAKER_THRESHOLD
        self._failures = 0
        self._open     = False

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.threshold:
            self._open = True
            log.warning("[circuit:%s] opened after %d consecutive failures",
                        self.name, self._failures)

    def record_success(self):
        if self._failures > 0:
            log.info("[circuit:%s] recovered after %d failure(s)",
                     self.name, self._failures)
        self._failures = 0
        self._open     = False

    @property
    def is_open(self) -> bool:
        return self._open


# ── OCR with retry ────────────────────────────────────────────────────────────

def _ocr_with_retry(
    path: Path,
    page_index: int,
    engine: str,
    circuit: CircuitBreaker,
) -> str:
    """
    OCR one scanned page with full resilience handling:
      - RPM limit    → wait retryDelay, retry up to OCR_MAX_RETRY times
      - Daily quota  → raise QuotaExhaustedError, open circuit
      - Transient    → exponential backoff, TRANSIENT_RETRY_COUNT attempts
      - Unknown      → raise immediately, no retry
    """
    if circuit.is_open:
        raise QuotaExhaustedError(
            f"Circuit open for '{circuit.name}' — daily quota exhausted. "
            f"Resume tomorrow or re-ingest with engine='tesseract'."
        )

    for attempt in range(config.OCR_MAX_RETRY):
        try:
            text = ocr.extract_scanned_page(path, page_index, engine=engine)
            circuit.record_success()
            return text

        except Exception as e:
            kind = classify_api_error(e)

            if kind == "rpm":
                delay = _retry_delay_from_error(e)
                log.warning(
                    "[ocr] RPM limit on p.%d — waiting %ds (attempt %d/%d)",
                    page_index + 1, delay, attempt + 1, config.OCR_MAX_RETRY
                )
                time.sleep(delay)

            elif kind == "quota":
                circuit.record_failure()
                raise QuotaExhaustedError(
                    f"Gemini daily quota exhausted at page {page_index + 1}"
                ) from e

            elif kind == "transient":
                # Do NOT record_failure() here — circuit breaker is quota-only.
                # A network blip must not open the circuit and block all future OCR.
                backoff = config.TRANSIENT_BACKOFF_BASE * (2 ** attempt)
                log.warning(
                    "[ocr] Transient error on p.%d — retrying in %ds: %s",
                    page_index + 1, backoff, e
                )
                time.sleep(backoff)
                if attempt == config.OCR_MAX_RETRY - 1:
                    raise   # exhausted transient retries

            else:
                raise       # unknown error — fail fast


    raise RuntimeError(
        f"Page {page_index + 1} failed after {config.OCR_MAX_RETRY} retries"
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug_title(path: Path) -> str:
    name = path.stem
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name.title()[:80]          # cap at 80 chars — keeps the UI clean


def _find_existing(source_file: Path) -> dict | None:
    """Return the existing book dict if this source path was already ingested."""
    for book in db.list_books():
        if book.get("source_file") == str(source_file):
            return book
    return None


# ── Public: ingest ────────────────────────────────────────────────────────────

def ingest(
    path: Path,
    engine: str = None,
    ocr_prompt: str = None,
) -> dict:
    """
    Full pipeline: file → OCR → SQLite.

    Args:
        path:       Absolute path to the file.
        engine:     'gemini' | 'tesseract'. Defaults to config.DEFAULT_OCR_ENGINE.
        ocr_prompt: Key into config.OCR_PROMPTS. Defaults to config.DEFAULT_OCR_PROMPT.

    Returns dict:
        {book_id, title, pages, duplicate: bool}
        On duplicate: also includes 'message' explaining what exists.

    Raises:
        QuotaExhaustedError — Gemini daily quota hit mid-ingest; partial book saved.
        ValueError          — Unsupported file type or unknown engine value.
    """
    engine     = engine     or config.DEFAULT_OCR_ENGINE
    ocr_prompt = ocr_prompt or config.DEFAULT_OCR_PROMPT

    if engine not in ("gemini", "tesseract"):
        raise ValueError(f"Unknown OCR engine: {engine!r}. Use 'gemini' or 'tesseract'.")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"Unsupported file type: {suffix}")

    title = _slug_title(path)

    # ── Duplicate detection ───────────────────────────────────────────────────
    existing = _find_existing(path)
    if existing:
        log.warning(
            "[ingest] Duplicate detected: '%s' already ingested as '%s' (id=%s)",
            path.name, existing["title"], existing["id"]
        )
        return {
            "book_id":   existing["id"],
            "title":     existing["title"],
            "pages":     existing.get("total_pages", 0),
            "duplicate": True,
            "message": (
                f"'{path.name}' was already ingested as '{existing['title']}'. "
                f"Delete the existing entry first if you want to re-ingest it."
            ),
        }

    book_id = str(uuid.uuid4())
    log.info("[ingest] '%s' | id=%s | engine=%s | prompt=%s",
             title, book_id, engine, ocr_prompt)

    # ── Plain text / Markdown ─────────────────────────────────────────────────
    if suffix in {".txt", ".md"}:
        page_texts = ocr.extract_from_text(path)
        db.insert_book(book_id, title, path, ocr_engine=engine)
        db.save_bookmark(book_id, 1)
        for i, text in enumerate(page_texts, start=1):
            db.insert_page(book_id, i, text)
        db.update_book_page_count(book_id)
        log.info("[ingest] ✅ '%s' — %d page(s)", title, len(page_texts))
        return {"book_id": book_id, "title": title,
                "pages": len(page_texts), "duplicate": False}

    # ── Single image ──────────────────────────────────────────────────────────
    if suffix in {".jpg", ".jpeg", ".png"}:
        text = ocr.extract_from_image(path, engine=engine, prompt_key=ocr_prompt)
        if engine != "tesseract":
            db.log_api_call("gemini_ocr")
        db.insert_book(book_id, title, path, ocr_engine=engine)
        db.save_bookmark(book_id, 1)
        db.insert_page(book_id, 1, text)
        db.update_book_page_count(book_id)
        log.info("[ingest] ✅ '%s' — 1 page", title)
        return {"book_id": book_id, "title": title, "pages": 1, "duplicate": False}

    # ── PDF ───────────────────────────────────────────────────────────────────
    if suffix == ".pdf":
        page_texts = ocr.extract_from_pdf(path, engine=engine)

        # Text-native PDF — fast path, no API calls.
        # Store "pdfplumber" explicitly — not the requested engine — because
        # pdfplumber did the actual work regardless of what engine was passed.
        # This shows correctly in logs and in future UI state identifiers.
        if page_texts is not None:
            db.insert_book(book_id, title, path, ocr_engine="pdfplumber")
            db.save_bookmark(book_id, 1)
            for i, text in enumerate(page_texts, start=1):
                db.insert_page(book_id, i, text)
            db.update_book_page_count(book_id)
            log.info("[ingest] ✅ '%s' (text-native) — %d page(s)", title, len(page_texts))
            return {"book_id": book_id, "title": title,
                    "pages": len(page_texts), "duplicate": False}

        # Scanned PDF — page by page with circuit breaker
        total   = ocr.get_pdf_page_count(path)
        circuit = CircuitBreaker(name="gemini_ocr")
        log.info("[ingest] '%s' is scanned — %d pages, engine=%s", title, total, engine)

        # Create the book row NOW — pages appear in sidebar as they arrive
        db.insert_book(book_id, title, path, ocr_engine=engine)
        db.save_bookmark(book_id, 1)
        log.info("[ingest] Book row created | id=%s", book_id)   # always log id at creation

        completed = 0
        for i in range(total):
            log.info("[ocr] Page %d/%d — '%s'", i + 1, total, title)

            try:
                text = _ocr_with_retry(path, i, engine, circuit)
                db.insert_page(book_id, i + 1, text.strip())
                db.update_book_page_count(book_id)
                if engine != "tesseract":
                    db.log_api_call("gemini_ocr")
                completed += 1

            except QuotaExhaustedError:
                # Daily quota hit — save what we have, surface the error clearly
                db.update_book_page_count(book_id)
                log.error(
                    "[ingest] QUOTA EXHAUSTED — '%s' stopped at page %d/%d. "
                    "Pages 1–%d are saved under id=%s. "
                    "Options: resume tomorrow with Gemini, or re-ingest with engine='tesseract'.",
                    title, i + 1, total, completed, book_id
                )
                raise   # caller (watcher or route) surfaces this to the user

            except Exception as e:
                # Non-quota failure on this page — log, insert placeholder, continue
                log.error("[ingest] Page %d failed (non-quota): %s", i + 1, e)
                db.insert_page(book_id, i + 1, f"[Page {i + 1} OCR failed: {e}]")

            # RPM delay between pages (skip after last page)
            if engine != "tesseract" and i < total - 1:
                log.debug("[ocr] RPM delay: %ds", config.RPM_DELAY)
                time.sleep(config.RPM_DELAY)

        db.update_book_page_count(book_id)
        log.info("[ingest] ✅ '%s' complete — %d/%d pages | id=%s",
                 title, completed, total, book_id)
        return {"book_id": book_id, "title": title, "pages": completed, "duplicate": False}
