# How to use Noëtone

A short guide to living with the app day to day. (For installation, see [README.md](README.md).)

---

## The one thing to remember

**Drop a file into the `inbox/` folder. That's the whole workflow.**

Noëtone watches that folder. Anything you put in is picked up automatically, turned into a listenable title, and shown in the sidebar. There's no "import" button and nothing to configure per file — the friction is meant to be zero.

Supported files: `.pdf`, `.jpg`, `.jpeg`, `.png`, `.txt`, `.md`.

---

## Listening

1. Click a title in the left sidebar to open it.
2. Press **Play** (the round button) or hit the **spacebar**.
3. Move around:
   - `←` / `→` arrows — previous / next page
   - **Speed dropdown** — 0.5× up to 2.5×
   - The app **saves your place automatically**; reopen any time and continue.
4. **Chapters:** click **+ chapter** to mark the current page as the start of a chapter and give it a name. Useful for long books.

The reader is intentionally calm — dark, muted, with the text as the brightest thing on screen. It's built not to over-stimulate.

---

## Getting the best results

**Match the OCR prompt to your material.** In `config.py`, set `DEFAULT_OCR_PROMPT` to the closest fit:

- `novel` — prose, stories, general books
- `textbook` — keeps code blocks, equations, technical terms
- `handwritten` — your own notes and handwritten pages

**Headers, footers, and page numbers.** By default these *are* read aloud, which helps you keep track of where you are in a physical book. If you find them annoying, add this line to your chosen prompt in `config.py`:

> "Do not include page numbers, running headers, or footers."

**Choosing quality vs. quota.** The premium Gemini voice has a small daily allowance. For plain digital PDFs and text where voice quality matters less, set `DEFAULT_TTS_ENGINE = "gtts"` in `config.py` to conserve your Gemini voice calls for scanned books and handwriting, where the quality difference is large.

---

## What to expect with longer documents

- **Digital PDFs and text files** are near-instant — no API calls needed for extraction.
- **Scanned PDFs and photos** go through Gemini OCR, which is rate-limited to a few pages per minute on the free tier. A long scanned book is a background job — start it and walk away.
- If a daily quota runs out mid-book, **everything processed so far is saved.** You'll see a message telling you where it stopped.

---

## Watching your daily usage

The bottom-left of the sidebar shows today's **OCR** and **TTS** call counts, and turns amber as you approach the free-tier limits. Limits reset at **midnight Pacific Time**.

---

## Re-processing a page at higher quality

If a page was generated with the basic voice (`.mp3`) and you want the premium Gemini voice instead, delete that page's audio file from the `audio/<book>/` folder and play the page again — it will regenerate.

---

## When something looks stuck

- **A scanned book is going slowly** — that's the free-tier rate limit (3 requests/minute), not a crash. Let it run.
- **"Quota exhausted" messages** — you've hit Google's daily ceiling. Resume tomorrow, or re-process that book with `DEFAULT_OCR_ENGINE = "tesseract"` (local, no quota, lower accuracy).
- **A file didn't appear** — check it's a supported type, and check `reader.log` in the project folder for details.
- **Re-dropping a book you already added** — the app skips duplicates; delete the existing title first if you want to re-ingest it.
