"""
app.py — Flask application entry point.
"""

import logging
import logging.handlers
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()   # must run before any module reads GEMINI_API_KEY


from flask import Flask, jsonify, render_template, request, send_file, make_response

import config
import db
import pipeline
import tts as tts_module

# ── Logging ───────────────────────────────────────────────────────────────────
# Configured once here, at the top, before anything else runs.
# Every module that calls logging.getLogger(__name__) automatically routes here.

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers = [
        logging.FileHandler(config.BASE_DIR / "reader.log"),
        logging.StreamHandler(),          # also prints to console
    ]
)
log = logging.getLogger(__name__)

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

config.INBOX_DIR.mkdir(exist_ok=True)
(config.INBOX_DIR / "done").mkdir(exist_ok=True)
config.AUDIO_DIR.mkdir(exist_ok=True)

# init_db() runs at module load time — works with both `python app.py` AND
# WSGI servers (gunicorn, waitress) which import the module without running __main__.
db.init_db()


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── Books API ─────────────────────────────────────────────────────────────────

@app.route("/books")
def list_books():
    return jsonify(db.list_books())


@app.route("/books/<book_id>")
def get_book(book_id):
    book = db.get_book(book_id)
    if not book:
        return jsonify({"error": "Not found"}), 404
    return jsonify(book)


@app.route("/books/<book_id>", methods=["DELETE"])
def delete_book(book_id):
    if not db.get_book(book_id):
        return jsonify({"error": "Not found"}), 404
    db.delete_book(book_id)
    return jsonify({"ok": True})


# ── Pages API ─────────────────────────────────────────────────────────────────

@app.route("/books/<book_id>/pages/<int:page_num>")
def get_page(book_id, page_num):
    page     = db.get_page(book_id, page_num)
    bookmark = db.get_bookmark(book_id)
    if not page:
        return jsonify({"error": "Page not found"}), 404
    return jsonify({**page, "current_bookmark": bookmark})


# ── Bookmarks API ─────────────────────────────────────────────────────────────

@app.route("/books/<book_id>/bookmark", methods=["POST"])
def save_bookmark(book_id):
    data     = request.get_json() or {}
    page_num = data.get("page")
    if not page_num:
        return jsonify({"error": "Missing page"}), 400
    db.save_bookmark(book_id, page_num)
    return jsonify({"ok": True})


# ── Chapters API ──────────────────────────────────────────────────────────────

@app.route("/books/<book_id>/chapters")
def get_chapters(book_id):
    return jsonify(db.get_chapters(book_id))


@app.route("/books/<book_id>/chapters", methods=["POST"])
def add_chapter(book_id):
    data       = request.get_json() or {}
    title      = data.get("title")
    start_page = data.get("start_page")
    if not title or start_page is None:
        return jsonify({"error": "Missing required fields: title, start_page"}), 400
    try:
        start_page = int(start_page)
    except (TypeError, ValueError):
        return jsonify({"error": "start_page must be an integer"}), 400
    db.insert_chapter(book_id, title, start_page)
    return jsonify({"ok": True})


@app.route("/chapters/<int:chapter_id>", methods=["DELETE"])
def delete_chapter(chapter_id):
    db.delete_chapter(chapter_id)
    return jsonify({"ok": True})


# ── Manual ingest (debug mode only) ──────────────────────────────────────────

@app.route("/ingest", methods=["POST"])
def manual_ingest():
    """
    POST {"path": "/absolute/path/to/file.jpg", "engine": "gemini"}
    Only available when Flask is running in debug mode.
    Useful for testing a specific file without dropping it into inbox.
    """
    if not app.debug:
        return jsonify({"error": "This endpoint is only available in debug mode"}), 403

    data      = request.get_json() or {}
    file_path = Path(data.get("path", ""))
    engine    = data.get("engine", config.DEFAULT_OCR_ENGINE)

    if not file_path.exists():
        return jsonify({"error": f"File not found: {file_path}"}), 404

    try:
        result = pipeline.ingest(file_path, engine=engine)
        return jsonify(result)
    except pipeline.QuotaExhaustedError as e:
        return jsonify({"error": str(e), "quota_exhausted": True}), 503
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.error("[ingest] Unexpected error: %s", e)
        return jsonify({"error": "Internal error — check reader.log"}), 500


# ── TTS API ───────────────────────────────────────────────────────────────────

@app.route("/tts", methods=["POST"])
def tts():
    """
    POST {"book_id": "...", "page": 1, "text": "..."}
    Cache-first: serves existing audio file if already generated.
    Generates + caches on first request for each page.
    """
    data    = request.get_json() or {}
    text    = (data.get("text") or "").strip()
    book_id = data.get("book_id", "")

    try:
        page = int(data.get("page", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "page must be an integer"}), 400

    if not text:
        return jsonify({"error": "No text provided"}), 400

    try:
        # Cache-first path: book_id + page known
        if book_id and page:
            cached = db.get_audio_path(book_id, page)
            if cached:
                mimetype = "audio/mpeg" if cached.endswith(".mp3") else "audio/wav"
                return send_file(cached, mimetype=mimetype)

            # DB tracking (audio_path, api_log) handled inside synthesize_cached
            audio_path, mimetype = tts_module.synthesize_cached(book_id, page, text)
            return send_file(str(audio_path), mimetype=mimetype)

        # Fallback: no book context — generate in memory, no caching
        # api_log written inside synthesize() via _synthesize_gemini on success
        audio_bytes, mimetype = tts_module.synthesize(text)
        resp = make_response(audio_bytes)
        resp.headers["Content-Type"]   = mimetype
        resp.headers["Content-Length"] = str(len(audio_bytes))
        resp.headers["Cache-Control"]  = "no-cache"
        return resp

    except Exception as e:
        log.error("[tts] Error: %s", e)
        return jsonify({"error": str(e)}), 500


# ── Usage API ─────────────────────────────────────────────────────────────────

@app.route("/api/usage")
def api_usage():
    return jsonify(db.get_daily_usage())


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from watcher import start_watcher
    start_watcher(config.INBOX_DIR)
    log.info("[app] Running → http://localhost:5000")
    app.run(debug=True, port=5000, use_reloader=False)
