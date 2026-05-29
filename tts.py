"""
tts.py — Text-to-speech with Gemini TTS + gTTS fallback.

Priority order is defined in config.GEMINI_TTS_MODELS (update there, not here).
Currently:
  1. gemini-2.5-flash-preview-tts  — primary (Kore voice preferred)
  2. gemini-3.1-flash-tts-preview  — fallback on quota hit
  3. gTTS via Google Translate      — free, unlimited, no API key

Per-book TTS engine override:
  Set config.DEFAULT_TTS_ENGINE = "gtts" when ingesting books that don't
  benefit from Gemini quality (text-native PDFs, simple reads).
  Saves your 10 Gemini calls/day for content that actually needs it.

Cached files:
  Gemini → audio/<book_id>/page_NNN.wav  (audio/wav)
  gTTS   → audio/<book_id>/page_NNN.mp3  (audio/mpeg)

To upgrade gTTS pages to Gemini quality later:
  Delete the .mp3 file(s) from the audio/<book_id>/ folder.
  Next playback request regenerates with Gemini automatically.
  DB audio_path is cleared on cache miss in db.get_audio_path().
"""

import base64
import io
import logging
import re
import wave
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types
from gtts import gTTS

import config
import db
from pipeline import classify_api_error

log = logging.getLogger(__name__)

# True constants — audio format, never changes, not in config
SAMPLE_RATE  = 24000
CHANNELS     = 1
SAMPLE_WIDTH = 2   # 16-bit PCM

_client = None
_gemini_exhausted_at: datetime | None = None   # set when all TTS models hit daily quota


def _gemini():
    global _client
    if _client is None:
        _client = genai.Client(
            http_options=types.HttpOptions(
                client_args={'timeout': config.API_TIMEOUT_SECONDS}
            )
        )
    return _client


def _gemini_available() -> bool:
    """
    Returns True if Gemini TTS quota may be available for this session.

    Gemini free-tier quota resets at midnight Pacific Time.
    Once all models return daily quota errors, Gemini is skipped for the
    remainder of the session — no wasted API calls, no latency on retries.
    If midnight PT has passed since exhaustion, the flag resets automatically.

    No API polling needed — purely time-based calculation using zoneinfo.
    zoneinfo is stdlib in Python 3.9+ — no pip install required.
    """
    global _gemini_exhausted_at
    if _gemini_exhausted_at is None:
        return True
    pt = ZoneInfo("America/Los_Angeles")
    now_pt       = datetime.now(pt)
    exhausted_pt = _gemini_exhausted_at.astimezone(pt)
    if now_pt.date() > exhausted_pt.date():
        log.info("[tts] Midnight PT passed — Gemini quota may have reset")
        _gemini_exhausted_at = None   # new day, new quota
        return True
    return False


# ── Gemini TTS ────────────────────────────────────────────────────────────────

def _call_gemini(model: str, text: str, voice: str) -> bytes:
    """Single Gemini TTS call. Returns WAV bytes. Raises on any error."""
    response = _gemini().models.generate_content(
        model=model,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice
                    )
                )
            )
        )
    )

    # Guard against unexpected API response structure.
    # If Gemini changes its response format, this raises a clear error
    # instead of a cryptic AttributeError or IndexError.
    try:
        raw = response.candidates[0].content.parts[0].inline_data.data
    except (IndexError, AttributeError) as e:
        raise RuntimeError(
            f"Unexpected Gemini TTS response structure from model '{model}'. "
            f"The API response format may have changed. "
            f"Check config.GEMINI_TTS_MODELS and the Gemini changelog. "
            f"Detail: {e}"
        ) from e

    if isinstance(raw, (bytes, bytearray)):
        pcm = raw
    else:
        padding = (4 - len(raw) % 4) % 4
        pcm = base64.b64decode(raw + '=' * padding)
    return _pcm_to_wav(pcm)


def _synthesize_gemini(text: str, voice: str = None) -> bytes:
    """
    Try each model in config.GEMINI_TTS_MODELS in order.
    Returns WAV bytes, or raises if all models are quota-exhausted.
    """
    voice    = voice or config.TTS_VOICE
    last_err = None

    if not config.GEMINI_TTS_MODELS:
        raise RuntimeError(
            "No TTS models configured. Add model names to config.GEMINI_TTS_MODELS."
        )

    for model in config.GEMINI_TTS_MODELS:
        try:
            wav = _call_gemini(model, text, voice)
            db.log_api_call(model)   # log per-model — not a generic "gemini_tts"
            log.info("[tts] Gemini ok (%s)", model)
            return wav
        except Exception as e:
            if classify_api_error(e) in ("quota", "rpm"):
                log.warning("[tts] %s quota hit — trying next model…", model)
                last_err = e
            else:
                raise   # non-quota error, don't swallow it

    # All models quota-exhausted — record timestamp for Pacific reset check
    global _gemini_exhausted_at
    _gemini_exhausted_at = datetime.now(timezone.utc)
    log.warning("[tts] All Gemini TTS models quota-exhausted. "
                "Gemini skipped for remainder of session. "
                "Quota resets at midnight Pacific Time.")
    raise last_err or RuntimeError("All Gemini TTS models exhausted")


# ── gTTS fallback ─────────────────────────────────────────────────────────────

def _synthesize_gtts(text: str) -> bytes:
    """Google Translate TTS. Returns MP3 bytes. Free, no API key required."""
    buf = io.BytesIO()
    try:
        gTTS(text=text, lang='en', slow=False).write_to_fp(buf)
    except Exception as e:
        raise RuntimeError(f"gTTS fallback also failed: {e}") from e
    buf.seek(0)
    return buf.read()


# ── Public: synthesize in memory ──────────────────────────────────────────────

def synthesize(text: str, voice: str = None, engine: str = None) -> tuple[bytes, str]:
    """
    Generate audio. Returns (audio_bytes, mimetype).

    engine: 'gemini' | 'gtts' | None (defaults to config.DEFAULT_TTS_ENGINE)

    To force gTTS for a specific call without touching config:
        synthesize(text, engine='gtts')
    """
    engine = engine or config.DEFAULT_TTS_ENGINE

    if engine == "gtts":
        return _synthesize_gtts(text), "audio/mpeg"

    # Skip Gemini entirely if daily quota is known exhausted for today
    if not _gemini_available():
        log.info("[tts] Gemini quota exhausted for today → gTTS directly")
        return _synthesize_gtts(text), "audio/mpeg"

    # Gemini with gTTS fallback on quota exhaustion OR transient/timeout errors
    try:
        return _synthesize_gemini(text, voice), "audio/wav"
    except Exception as e:
        kind = classify_api_error(e)
        if kind in ("quota", "rpm", "transient") or "timed out" in str(e).lower():
            log.warning("[tts] Gemini failed (%s: %s) → gTTS fallback", kind, e)
            return _synthesize_gtts(text), "audio/mpeg"
        raise


# ── Public: synthesize + cache to disk ────────────────────────────────────────

def synthesize_cached(
    book_id: str,
    page_num: int,
    text: str,
    engine: str = None,
) -> tuple[object, str]:
    """
    Generate audio and cache to disk. Returns (path, mimetype).
    Checks disk before hitting any API.

    Folder name format: audio/<slug>_<first8-of-uuid>/page_NNNN.ext
    Human-readable so users can find and manage their audio files directly.
    Example: audio/the-stranger_a3f9bc12/page_0042.wav

    To upgrade cached gTTS (.mp3) pages to Gemini quality (.wav):
    Delete the .mp3 file from the book's audio folder and request the page again.
    db.get_audio_path() already handles missing files gracefully.

    engine: 'gemini' | 'gtts' | None (defaults to config.DEFAULT_TTS_ENGINE)
    """
    # Build human-readable folder name from book title + first 8 chars of UUID.
    # Falls back to bare UUID if book record is missing (should not happen in practice).
    book = db.get_book(book_id)
    if book and book.get("title"):
        slug = re.sub(r"[^a-z0-9]+", "-", book["title"].lower()).strip("-")[:40]
        folder_name = f"{slug}_{book_id[:8]}"
    else:
        folder_name = book_id

    out_dir = config.AUDIO_DIR / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"page_{page_num:04d}"

    # Cache hit — check both formats
    if base.with_suffix(".wav").exists():
        return base.with_suffix(".wav"), "audio/wav"
    if base.with_suffix(".mp3").exists():
        return base.with_suffix(".mp3"), "audio/mpeg"

    # Generate and cache
    audio_bytes, mimetype = synthesize(text, engine=engine)
    ext  = ".wav" if mimetype == "audio/wav" else ".mp3"
    path = base.with_suffix(ext)
    path.write_bytes(audio_bytes)
    db.save_audio_path(book_id, page_num, str(path))  # DB tracks what's on disk
    log.info("[tts] Cached → %s (%s)", path.name, mimetype)
    return path, mimetype


# ── Internal ──────────────────────────────────────────────────────────────────

def _pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()
