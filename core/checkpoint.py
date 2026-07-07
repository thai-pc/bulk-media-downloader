"""Checkpoint store — state persistence & resume (SPEC section 4.6).

A single JSON object maps ``sha256(url)`` done-keys to metadata. Writes are
atomic (temp file + ``os.replace``) and guarded by a lock for concurrent
``mark_done`` calls from worker threads.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.queue_manager import Job

logger = logging.getLogger(__name__)

CHECKPOINT_VERSION = 1


class CheckpointStore:
    """Load/save completed-download state keyed by ``sha256(url)``."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._entries: dict[str, dict] = {}
        self._lock = threading.Lock()

    def load(self) -> None:
        """Read the JSON file into memory.

        A missing or corrupt file results in an empty store and a logged
        warning rather than an exception.
        """
        if not self.path or not os.path.exists(self.path):
            self._entries = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            entries = data.get("entries", {})
            if not isinstance(entries, dict):
                raise ValueError("entries is not an object")
            self._entries = entries
            logger.info("checkpoint loaded: %d entries", len(self._entries))
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.warning(
                "checkpoint file %s is missing/corrupt (%s); starting empty",
                self.path, exc,
            )
            self._entries = {}

    def is_done(self, url_hash: str) -> bool:
        """Return ``True`` if ``url_hash`` is recorded as completed."""
        return url_hash in self._entries

    def mark_done(self, job: "Job") -> None:
        """Record ``job`` as completed and persist atomically."""
        entry = {
            "url": job.url,
            "platform": str(job.platform.value),
            "output_path": job.output_path,
            "title": job.title,
            "finished_at": job.finished_at,
        }
        with self._lock:
            self._entries[job.url_hash] = entry
            self._flush_locked()

    def remove(self, url_hash: str) -> None:
        """Delete an entry (if present) and persist."""
        with self._lock:
            if url_hash in self._entries:
                del self._entries[url_hash]
                self._flush_locked()

    def clear(self) -> None:
        """Remove all entries and persist an empty store."""
        with self._lock:
            self._entries = {}
            self._flush_locked()

    def all(self) -> dict[str, dict]:
        """Return a shallow copy of all entries."""
        with self._lock:
            return dict(self._entries)

    # ----- internals -------------------------------------------------------

    def _flush_locked(self) -> None:
        """Write the store atomically. Caller must hold ``self._lock``."""
        if not self.path:
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        payload = {"version": CHECKPOINT_VERSION, "entries": self._entries}
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
