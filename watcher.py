"""
watcher.py — Watchdog inbox monitor.
Detects new files in /inbox, hands them to pipeline.ingest().

This module owns ONLY filesystem monitoring logic.
It does not own OCR, database writes, or retry logic.
All of that lives in pipeline.py.
"""

import logging
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import config
import pipeline

log = logging.getLogger(__name__)

SUPPORTED = {".jpg", ".jpeg", ".png", ".pdf", ".txt", ".md"}


class InboxHandler(FileSystemEventHandler):

    def __init__(self, inbox: Path, engine: str = None):
        self.inbox  = inbox
        self.engine = engine or config.DEFAULT_OCR_ENGINE

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if "done" in path.parts:
            return
        if path.suffix.lower() not in SUPPORTED:
            log.info("[watcher] Skipping unsupported: %s", path.name)
            return

        log.info("[watcher] Detected: %s", path.name)
        try:
            result = pipeline.ingest(path, engine=self.engine)

            if result.get("duplicate"):
                # File already in library — warn and leave in inbox.
                # User can delete the existing entry and drop again to re-ingest.
                log.warning("[watcher] %s", result["message"])
                return

            _move_to_done(path)

        except pipeline.QuotaExhaustedError as e:
            # Daily quota exhausted mid-ingest. Partial book is saved.
            # File stays in inbox — user can re-drop tomorrow or switch engine.
            log.error(
                "[watcher] Quota exhausted for %s: %s — file left in inbox for retry",
                path.name, e
            )
        except Exception as e:
            log.error(
                "[watcher] Ingest failed for %s: %s — file left in inbox",
                path.name, e
            )


def _move_to_done(path: Path):
    done = path.parent / "done" / path.name
    try:
        path.rename(done)
        log.info("[watcher] Moved → done/%s", path.name)
    except Exception as e:
        log.error("[watcher] Could not move %s: %s", path.name, e)


def start_watcher(inbox: Path, engine: str = None) -> Observer:
    """
    Start the inbox file watcher. Returns the Observer instance.
    Observer is itself a Thread — call .start() directly, no wrapper needed.
    """
    handler  = InboxHandler(inbox, engine=engine)
    observer = Observer()
    observer.schedule(handler, str(inbox), recursive=False)
    observer.start()    # Observer IS a Thread — start() is non-blocking
    log.info("[watcher] Watching %s (engine=%s)",
             inbox, engine or config.DEFAULT_OCR_ENGINE)
    return observer
