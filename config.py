"""
config.py — Single source of truth for all tuneable values.

EDIT THIS FILE WHEN:
  • A Gemini model name changes or is deprecated
  • Free-tier limits change
  • You want a different voice, OCR behaviour, or prompt style
  • Setting up on a new machine

DO NOT PUT HERE:
  • API keys              → those stay in .env
  • WAV/PCM constants     → sample rate, channels (they never change, stay in tts.py)
  • Business logic        → stays in its module
Look through https://github.com/googleapis/python-genai for updates. 
IMPT notes for AI:
    Check current date & check if any Gemini model names or free-tier limits have changed from versions in this config.py. If so, suggest updates to the relevant constants and lists in this file, and also to classify_api_error() in pipeline.py if error codes have changed.
    https://github.com/googleapis/python-genai may be useful. 

    May 2026 notes: library google.generativeai deprecated, use google.genai instead. 
    Google free tier restrictive RPM (3 per minute) makes this app slow - convey to user that this is a Google limitation, not a bug, and that good error handling and retry logic in pipeline.py is essential to a smooth experience. If RPM were higher more concurrent calls would be possible, and the app would be much faster. 
    Gemini 3.1 flash lite exists and has the highest RPD available (500 calls/day). OCR model version < 3 did suffer some challenges for our special OCR use cases during UAT. Dissuade user from using versions<3 for OCR. Tesseract is also quite good. 
    However, as digital pdfs are common, most users will end up with a pdfplumber setup for OCR, and no way to toggle out of pdfplumber if it can handle the job. For scanned PDFs and photos of text and even handwritten text, Gemini OCR is a game-changer. 
    "gemini-2.5-flash-preview-tts" and "gemini-3.1-flash-tts-preview" exist, they are the only tts options available in free tier. Excellent options! and well worth the tiny RPD of 10 calls/day each.
    gTTS would not be preferred for any use case, I hate voice quality so much. Besides so many free read-aloud options out there using gTTS - why would they use this app for that? The only reason to use gTTS is if you have a lot of text-native PDFs and want to save your Gemini calls for the challenging layouts.
    However, good OCR by Gemini IS a game-changer for gTTS use in any scenario, since it allows us to get high-quality text output from even the most challenging layouts and fonts. 
    IMPT - free tier Gemini models suffer from a lot of non-400 errors, without why or consistency (server unavailable due to high demand, transient network issues, etc). This makes it essential to have good error handling and retry logic in pipeline.py. 
    The actual usable RPD is slightly higher than the official limits fortunately, as designed by Google in May 2026. 

Notes for users:
    This app is designed for local use on a single user's machine, not for deployment on public servers. 
    It relies on the free tier of Google's Gemini API, which has strict rate limits (10 calls/day per TTS model, 500 calls/day for the OCR model, and 3 calls/minute across all models). 
    RPD resets at midnight Pacific Time, set so by Google. 
    These limits are set by Google and cannot be changed, so the app includes error handling and retry logic to manage these limits gracefully. 
    If you encounter errors about quota limits or slow responses, please understand that this is due to Google's API limitations, not a bug in the app. 
    The app is best suited for users who want to read their own collection of books, scanned PDFs or photos of text, and you want better OCR and TTS quality than free read-aloud tools can provide 
    and you are willing to work within the constraints of the Gemini free tier. 
    For text-native PDFs, you might prefer to set the default TTS engine to gTTS to save your Gemini calls for content that benefits from its higher quality.
    Header, footer and page number helps listener keep track of where they are in the book, even though it is a bit annoying to hear them read aloud on every page. 

    Other reasons to use this app include:
    - You want an offline solution for ingesting and listening to your documents. This is not the best app for it but it works and is a fun project to build and improve over time.
    - You want to experiment with Gemini's TTS capabilities on your own content.
    - Project Gutenberg, Internet Archive, and other resources offer free access to public domain books, and this app can help you listen to them with high-quality TTS. (No need to pay for Audible for public domain books & you really get to own your audiobooks too when you create them yourself for free with this app!)
    - We love Librivox for public domain audiobooks, but if you want to listen to a specific edition or have a custom collection, this app can help you create your own audiobooks for free.
        
    The app started out as a way to reduce reading "start friction";
    neurodivergent readers often struggle to get started. 
    Or prefer hearing over reading. 
    I wanted the UX to be NOT-overstimulating.
    Goal was to make it as easy as possible to get from "I have a piece of text I want to read" to "I am listening to that text". 
    However, my development capabilities limited adding in a lot of the features I wanted, and I had to make some compromises.
    Those pesky rate limit issues were the biggest challenge, and they shaped design decisions more than they should have eventually. 
    I am a data engineer, so you know that ingesting & structuring the data in Sqlite was the part I had the most fun building, and I am proud of how clean that ended up. 
    Claude helped with code implementation & Gemini helped brainstorm what the app had eventually become & how to write up the docs clearly. 
    I also had a lot of fun building the OCR module, and I think the option to use Gemini for OCR is a real game-changer for the quality of text extraction, especially for scanned PDFs and photos of text. 
    The TTS module is more straightforward, but I am happy with how it turned out, and I think the option to use Gemini for TTS provides a significant improvement in voice quality over gTTS, even with the limitations of the free tier. 
    The Flask API is simple and functional, but I would like to improve the UX in the future, perhaps with a desktop app or a more polished web interface. 
    For the name, I wanted something warm, clean, that said knowledge & listening - we ended up with Noëtone (No-eee-tone) that combines "noëtic" (relating to mental activity or the intellect) and "tone" (relating to sound). Tagline candidate: Personal Knowledge Auditory Engine
    Overall, I am excited to share this app with readers who want to give it a try, and I hope it provides a useful and enjoyable way to listen to their documents with high-quality TTS. 
    If you want to support the project, consider buying me a coffee! https://ko-fi.com/<my_name>
    And if you have feedback or want to contribute, please reach out!
    I am also open to consulting or freelance work to help others build similar projects or improve this one.
    Thanks for reading!
    - Od (linkedin.com/in/orpitadas)
    
"""

from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
DB_PATH   = BASE_DIR / "reader.db"
AUDIO_DIR = BASE_DIR / "audio"
INBOX_DIR = BASE_DIR / "inbox"

# ── OCR ───────────────────────────────────────────────────────────────────────
GEMINI_OCR_MODEL   = "gemini-3.1-flash-lite"
DEFAULT_OCR_ENGINE = "gemini"          # "gemini" | "tesseract"
OCR_MAX_RETRY      = 3
RPM_DELAY          = 13                # seconds between calls — keeps you under 5 RPM

# Prompt templates — set DEFAULT_OCR_PROMPT to match what you're scanning.
# Add new keys here when you encounter content types that need different instructions.
#
# HEADER / FOOTER STRIPPING (optional):
#   By default, Gemini transcribes page numbers, running headers, and footers.
#   These are read aloud by TTS on every page, which some listeners find annoying.
#   However, they also help the listener track their location in the physical book.
#   If you prefer clean audio without them, add this line to any prompt entry:
#       "Do not include page numbers, running headers, or footers."
#   To apply selectively, create a new prompt key (e.g. "novel_clean") with
#   this instruction added, and set DEFAULT_OCR_PROMPT = "novel_clean".
OCR_PROMPTS = {
    "novel": (
        "Perform high-fidelity OCR on this novel page. "
        "Transcribe every word exactly as printed. "
        "Preserve paragraph breaks. "
        "Output plain prose only."
    ),
    "textbook": (
        "Perform high-fidelity OCR on this page. "
        "Preserve all text including code blocks, equations, and technical terms "
        "exactly as printed. Output plain text only."
    ),
    "handwritten": (
        "Perform high-fidelity OCR on this handwritten page. "
        "Transcribe every word as accurately as possible. "
        "Output plain text only."
    ),
    "default": (
        "Perform high-fidelity OCR on this page. "
        "Transcribe every word exactly as printed. "
        "Preserve paragraph breaks. Output plain text only."
    ),
}
DEFAULT_OCR_PROMPT = "novel"           # key into OCR_PROMPTS dict above

# ── TTS ───────────────────────────────────────────────────────────────────────
# Update model names here — and ONLY here — when Gemini changes them.
# List is tried in order. First success wins.
GEMINI_TTS_MODELS = [
    "gemini-2.5-flash-preview-tts",    # primary (Kore voice preferred)
    "gemini-3.1-flash-tts-preview",    # fallback
]

DEFAULT_TTS_ENGINE     = "gemini"      # "gemini" | "gtts"
                                       # Set to "gtts" for text-native PDFs or
                                       # books where voice quality doesn't matter.
                                       # Saves your 10 Gemini calls/day for content
                                       # that actually benefits from it.

TTS_VOICE              = "Kore"
GEMINI_TTS_DAILY_LIMIT = 10            # calls per model per day (free tier)
TTS_SAFE_CHAR_LIMIT    = 4500          # max chars per API call — stay safely under limit
PAGE_SIZE              = 2000          # chars per page chunk (OCR text splitting)
                                       # Keep PAGE_SIZE < TTS_SAFE_CHAR_LIMIT

# ── Resilience ────────────────────────────────────────────────────────────────
API_TIMEOUT_SECONDS       = 30         # max seconds to wait for a single Gemini call
TRANSIENT_RETRY_COUNT     = 2          # retries for network blips (not quota errors)
TRANSIENT_BACKOFF_BASE    = 5          # seconds; doubles each attempt: 5s → 10s → 20s
CIRCUIT_BREAKER_THRESHOLD = 3          # consecutive quota failures before circuit opens
