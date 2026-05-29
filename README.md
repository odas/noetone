# Noëtone

**Your reading, read to you.** Drop in a PDF, a scanned page, a photo of your own handwritten notes, or a text file — Noëtone turns it into an audiobook in a natural voice, and saves every audio file on your own machine.

Built for people with a growing reading backlog and shrinking screen time: researchers, students, and anyone who finds *starting* a dense document harder than the reading itself. (It started as a tool for my own ADHD brain — listening removes the "start friction" that staring at a 40-page PDF creates.)

> **What it needs:** Noëtone uses Google's free Gemini API for its natural-sounding voice and for reading scanned and handwritten pages, so you'll need an internet connection and a free Gemini API key (about a minute to set up — see below). Every audio file it makes is **saved on your own machine and is yours to keep.** Since the pages you process pass through Google's API to be transcribed and read aloud, use it for material you're comfortable putting through a cloud service — I run my study notes and public-domain books through it daily; I don't feed it my private journal.

---

## What it does

- **Reads almost anything.** Digital PDFs, scanned PDFs, photos of pages (`.jpg`/`.png`), plain text, and Markdown.
- **Genuinely good on hard inputs.** Gemini OCR handles messy scans and *handwriting* far better than free read-aloud tools.
- **Drop-folder workflow.** Put a file in the `inbox/` folder and it's picked up and processed automatically — no menus, no config per file.
- **A calm, low-stimulation reader.** Muted dark interface where only the text is bright. Play/pause, speed control, bookmark, and resume-where-you-left-off.
- **You own the output.** Audio is cached as standard `.wav`/`.mp3` files in the `audio/` folder.

---

## Before you start

Noëtone is a small Python app you run on your own computer (designed for single-user local use, not for hosting on a public server). You'll need:

1. **Python 3.9 or newer.**
2. **A free Google Gemini API key** — get one at <https://aistudio.google.com/app/apikey>.
3. **Two small system tools** that the OCR step depends on:
   - **Tesseract** (offline OCR engine)
   - **Poppler** (lets the app turn PDF pages into images)

### Installing the two system tools

| Your OS | Command |
|---|---|
| **macOS** (with [Homebrew](https://brew.sh)) | `brew install tesseract poppler` |
| **Ubuntu / Debian** | `sudo apt install tesseract-ocr poppler-utils` |
| **Windows** | Install [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) and [Poppler](https://github.com/oschwartz10612/poppler-windows/releases), then add both to your PATH |

---

## Setup (step by step)

These steps assume you can use Git and a terminal, but **don't** assume you can code. Copy-paste each line.

```bash
# 1. Get the code
git clone https://github.com/odas/noetone.git
cd noetone

# 2. (Recommended) make a private space for the app's dependencies
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install the Python dependencies
pip install -r requirements.txt
```

**4. Add your API key.** Create a file named `.env` in the project folder with this one line (paste your real key):

```
GEMINI_API_KEY=your_key_here
```

**5. Run it.**

```bash
python app.py
```

Then open **<http://localhost:5000>** in your browser. To stop the app, press `Ctrl+C` in the terminal.

That's it. The first run creates the `inbox/`, `audio/`, and database files for you automatically.

---

## Your first audiobook

1. With the app running, drop a file (PDF, image, `.txt`, or `.md`) into the **`inbox/`** folder.
2. Within a few seconds it appears in the sidebar. (Scanned PDFs take longer — see the note on speed below.)
3. Click it, then press **Play** (or the spacebar). Use `←` / `→` to move between pages and the dropdown to change speed.
4. Close the app and come back later — it remembers where you stopped.

Full operating guide: see **[HOW_TO_USE.md](HOW_TO_USE.md)**.

---

## A note on speed (this is Google, not a bug)

Noëtone runs on Google's **free** Gemini tier, which is deliberately rate-limited:

- **3 requests per minute** across all models
- **~500 OCR requests/day**, and **~10 voice requests/day per voice model**
- Limits reset at **midnight Pacific Time**

So a long scanned book is processed slowly, in the background, and is best left to run while you do something else. The app handles these limits gracefully: it backs off and retries on temporary errors, and if the daily quota runs out mid-book it **saves everything done so far** and tells you where it stopped. This resilience layer is the real engineering heart of the project. If you hit "quota" messages, that's Google's ceiling, not a fault in the app.

**Tip:** for plain digital PDFs and text files (where voice quality matters less), set `DEFAULT_TTS_ENGINE = "gtts"` in `config.py` to save your premium Gemini voice quota for the material that actually benefits from it.

---

## Things you can safely change (`config.py`)

Everything tuneable lives in `config.py`, with comments. The most useful knobs:

- **`TTS_VOICE`** — which Gemini voice to use (default `"Kore"`).
- **`DEFAULT_TTS_ENGINE`** — `"gemini"` (best quality) or `"gtts"` (saves quota).
- **`DEFAULT_OCR_ENGINE`** — `"gemini"` (best, esp. for scans/handwriting) or `"tesseract"` (fully local, no quota, lower accuracy).
- **`OCR_PROMPTS` / `DEFAULT_OCR_PROMPT`** — tell the OCR what it's looking at (`novel`, `textbook`, `handwritten`). You can also add an instruction here to **drop or keep** page numbers, headers, and footers, depending on whether you like hearing them.

---

## Roadmap (planned, not built yet)

These are real intentions for the next iterations, listed honestly as *not present today*:

- **Offline-capable voice** — a local TTS engine as a first-class option, so the app can run with no cloud and no quota at all (this is what will make a true "private, on your machine" mode possible).
- **Resume an interrupted scan** — re-dropping a partially-processed book continues from where it stopped instead of starting over.
- **Batch & stitching** — dropping many page-images, or a book scanned in sections, and having them combine into one title instead of many.
- **Validated on dense academic PDFs** — testing and tuning the OCR-prompt layer on multi-column whitepapers and papers, the original target use case.

---

## How it's built

A small Flask app with a clean separation of concerns:

- `app.py` — web routes and entry point
- `watcher.py` — watches the `inbox/` folder
- `pipeline.py` — ingestion, error classification, retry/backoff, and the circuit breaker
- `ocr.py` — text extraction (Gemini / Tesseract / pdfplumber)
- `tts.py` — speech synthesis (Gemini, with a gTTS fallback) and audio caching
- `db.py` — SQLite schema and all persistence
- `config.py` — every tuneable value, in one place

---

## Credits & contact

Built by **Orpita Das** — ex-Snowflake data engineer, in Pune, India. Code implementation done in collaboration with Claude; documentation and product brainstorming with Claude and Gemini.

- Feedback, ideas, or a bug? Open an issue or reach out.
- Like it? You can [buy me a coffee](https://ko-fi.com/) ☕
- Open to freelance and contract data work — [LinkedIn](https://linkedin.com/in/orpitadas)

*The name blends "noëtic" (of the mind/intellect) and "tone" (sound).*
# noetone
