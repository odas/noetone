"""
db.py — SQLite schema + all database operations.
Single source of truth for everything persisted.
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

log = logging.getLogger(__name__)


# ── Connection ────────────────────────────────────────────────────────────────

@contextmanager
def get_conn():
    """
    Context manager that yields an open SQLite connection.
    Commits on clean exit. Rolls back and re-raises on any exception.
    Always closes — no file handle leaks.

    Usage (unchanged from before):
        with get_conn() as conn:
            conn.execute(...)
    """
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")   # enforce ON DELETE CASCADE
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()                           # always closes — no leaks


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db():
    # WAL mode is a persistent DB-level setting that must be set outside
    # a transaction. We use a raw connection for this one pragma only.
    _set_wal()

    # Schema creation + migration in a single atomic transaction.
    # If this is interrupted mid-way, the whole thing rolls back cleanly.
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                source_file TEXT,
                total_pages INTEGER DEFAULT 0,
                added_at    TEXT NOT NULL,
                ocr_engine  TEXT DEFAULT 'gemini'
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id     TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                raw_text    TEXT NOT NULL,
                audio_path  TEXT,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
                UNIQUE (book_id, page_number)
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chapters (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id     TEXT NOT NULL,
                title       TEXT NOT NULL,
                start_page  INTEGER NOT NULL,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
                UNIQUE (book_id, title, start_page)
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bookmarks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id      TEXT UNIQUE NOT NULL,
                current_page INTEGER DEFAULT 1,
                updated_at   TEXT NOT NULL,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                service   TEXT NOT NULL,
                called_at TEXT NOT NULL,
                month     TEXT NOT NULL
            )""")

        # Migration: add audio_path if upgrading from an earlier schema version.
        # Safe to run every time — only fires if the column is missing.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(pages)").fetchall()]
        if "audio_path" not in cols:
            conn.execute("ALTER TABLE pages ADD COLUMN audio_path TEXT")
            log.info("[db] Migrated: added audio_path column")

    log.info("[db] Initialised → %s", config.DB_PATH)


def _set_wal():
    """Set WAL journal mode. Persistent on the DB file. Called once at init."""
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()


# ── Audio cache ───────────────────────────────────────────────────────────────

def get_audio_path(book_id: str, page_number: int):
    """Return cached audio path string if the file exists on disk, else None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT audio_path FROM pages WHERE book_id = ? AND page_number = ?",
            (book_id, page_number)
        ).fetchone()
    if row and row["audio_path"]:
        from pathlib import Path
        path = Path(row["audio_path"])
        return str(path) if path.exists() else None
    return None


def save_audio_path(book_id: str, page_number: int, audio_path: str):
    """Store the path to a cached audio file for a page."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE pages SET audio_path = ? WHERE book_id = ? AND page_number = ?",
            (audio_path, book_id, page_number)
        )


# ── Books ─────────────────────────────────────────────────────────────────────

def insert_book(book_id, title, source_file, ocr_engine="gemini"):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO books (id, title, source_file, added_at, ocr_engine)
               VALUES (?, ?, ?, ?, ?)""",
            (book_id, title, str(source_file),
             datetime.now(timezone.utc).isoformat(), ocr_engine)
        )


def update_book_page_count(book_id):
    with get_conn() as conn:
        conn.execute(
            """UPDATE books SET total_pages =
               (SELECT COUNT(*) FROM pages WHERE book_id = ?) WHERE id = ?""",
            (book_id, book_id)
        )


def list_books():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT b.*, bm.current_page
               FROM books b
               LEFT JOIN bookmarks bm ON bm.book_id = b.id
               ORDER BY b.added_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_book(book_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM books WHERE id = ?", (book_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_book(book_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))


# ── Pages ─────────────────────────────────────────────────────────────────────

def insert_page(book_id, page_number, raw_text):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO pages (book_id, page_number, raw_text)
               VALUES (?, ?, ?)
               ON CONFLICT (book_id, page_number) DO UPDATE
               SET raw_text = excluded.raw_text""",
            (book_id, page_number, raw_text)
        )


def get_page(book_id, page_number):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pages WHERE book_id = ? AND page_number = ?",
            (book_id, page_number)
        ).fetchone()
    return dict(row) if row else None


# ── Bookmarks ─────────────────────────────────────────────────────────────────

def save_bookmark(book_id, page_number):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO bookmarks (book_id, current_page, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT (book_id) DO UPDATE
               SET current_page = excluded.current_page,
                   updated_at   = excluded.updated_at""",
            (book_id, page_number, datetime.now(timezone.utc).isoformat())
        )


def get_bookmark(book_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT current_page FROM bookmarks WHERE book_id = ?", (book_id,)
        ).fetchone()
    return row["current_page"] if row else 1


# ── Chapters ──────────────────────────────────────────────────────────────────

def insert_chapter(book_id, title, start_page):
    with get_conn() as conn:
        # OR IGNORE: calling this twice with the same args is safe
        conn.execute(
            "INSERT OR IGNORE INTO chapters (book_id, title, start_page) VALUES (?, ?, ?)",
            (book_id, title, start_page)
        )


def get_chapters(book_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM chapters WHERE book_id = ? ORDER BY start_page",
            (book_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_chapter(chapter_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM chapters WHERE id = ?", (chapter_id,))


# ── API Usage ─────────────────────────────────────────────────────────────────

def log_api_call(service: str):
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO api_log (service, called_at, month) VALUES (?, ?, ?)",
            (service, datetime.now(timezone.utc).isoformat(), month)
        )


def get_daily_usage():
    """
    Return API call counts for today, reset at midnight Pacific Time.
    Matches Gemini's actual quota reset schedule.
    zoneinfo is stdlib in Python 3.9+ — no pip install required.
    """
    from zoneinfo import ZoneInfo
    pt = ZoneInfo("America/Los_Angeles")
    now_pt = datetime.now(pt)
    start_of_day_pt = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day_utc = start_of_day_pt.astimezone(timezone.utc).isoformat()

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT service, COUNT(*) as count FROM api_log
               WHERE called_at >= ? GROUP BY service""",
            (start_of_day_utc,)
        ).fetchall()
    usage = {"gemini_ocr": 0, "gemini_tts": 0}
    for r in rows:
        usage[r["service"]] = r["count"]
    return usage
