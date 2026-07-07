"""Free-proxy pool with rotation, health-check, and cooldown.

This module gives the anti-blocking layer a rotating pool of **free public**
proxies fetched from several community-maintained lists. It is Qt-free and uses
only the standard library (``urllib``) so it adds no hard dependency.

Design notes
------------
* Rotation is round-robin over entries that are not on cooldown; :meth:`get`
  returns ``None`` when nothing is available so callers fall back to a direct
  connection instead of failing.
* A proxy that fails a download is put on cooldown via :meth:`mark_bad`, and is
  dropped entirely after ``max_fails`` consecutive failures.
* :meth:`validate` optionally health-checks proxies concurrently and keeps only
  the ones that actually respond — free proxies are mostly dead, so this is
  strongly recommended before a large batch.

.. warning::
   Free public proxies are unreliable, slow, and a security risk: some log
   traffic, inject content, or are honeypots. Never send credentials or private
   data through them. For serious large-volume work use paid residential/mobile
   proxies (drop their endpoints into ``proxy_sources``).
"""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Optional

if TYPE_CHECKING:
    from core.config import Settings

logger = logging.getLogger(__name__)

# Built-in free proxy sources. Each returns plain text, one proxy per line, as
# either ``ip:port`` or ``scheme://ip:port``; both formats are normalized.
# Verified reachable 2026-07. Refresh these URLs if a source goes stale.
DEFAULT_SOURCES: tuple[str, ...] = (
    "https://api.proxyscrape.com/v4/free-proxy-list/get"
    "?request=display_proxies&proxy_format=protocolipport&format=text&protocol=http",
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/ProxyScraper/ProxyScraper/main/http.txt",
)

# A lightweight endpoint that returns HTTP 204 with an empty body — ideal for a
# fast liveness check through a candidate proxy.
DEFAULT_TEST_URL = "http://www.gstatic.com/generate_204"

_FETCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_IP_PORT_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}$")


@dataclass
class _Entry:
    """A single proxy and its health bookkeeping."""

    url: str
    fails: int = 0
    cooldown_until: float = 0.0


def _normalize(line: str, scheme: str) -> Optional[str]:
    """Normalize one source line to ``scheme://ip:port`` or return ``None``.

    Accepts bare ``ip:port`` and ``http(s)://ip:port``. SOCKS and non-IP hosts
    are skipped because the download engines use these as HTTP(S) proxies.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "://" in line:
        proto, _, rest = line.partition("://")
        if proto.lower() not in ("http", "https"):
            return None
        line = rest
    line = line.strip().rstrip("/")
    if not _IP_PORT_RE.match(line):
        return None
    return f"{scheme}://{line}"


class ProxyPool:
    """A thread-safe, rotating pool of free HTTP proxies."""

    def __init__(
        self,
        sources: Optional[Iterable[str]] = None,
        scheme: str = "http",
        fetch_timeout: float = 15.0,
        cooldown: float = 120.0,
        max_fails: int = 3,
    ) -> None:
        self.sources = tuple(sources) if sources else DEFAULT_SOURCES
        self.scheme = scheme
        self.fetch_timeout = fetch_timeout
        self.cooldown = cooldown
        self.max_fails = max_fails

        self._entries: list[_Entry] = []
        self._index = 0
        self._lock = threading.Lock()

    # ----- population ------------------------------------------------------

    def refresh(self) -> int:
        """Fetch every source, replace the pool, and return the proxy count."""
        collected: dict[str, None] = {}  # ordered dedupe
        for src in self.sources:
            try:
                for raw in self._fetch(src):
                    proxy = _normalize(raw, self.scheme)
                    if proxy:
                        collected.setdefault(proxy, None)
            except Exception as exc:  # noqa: BLE001 - one bad source must not stop us
                logger.warning("proxy source failed (%s): %s", src, exc)
        with self._lock:
            self._entries = [_Entry(url=u) for u in collected]
            self._index = 0
        logger.info("proxy pool refreshed: %d proxies from %d source(s)",
                    len(collected), len(self.sources))
        return len(collected)

    def _fetch(self, url: str) -> list[str]:
        """Download one source URL and return its non-empty lines."""
        req = urllib.request.Request(url, headers={"User-Agent": _FETCH_UA})
        with urllib.request.urlopen(req, timeout=self.fetch_timeout) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        return [ln for ln in text.splitlines() if ln.strip()]

    def add_proxies(self, proxies: Iterable[str]) -> int:
        """Add extra proxies (e.g. paid endpoints) to the pool; return added."""
        added = 0
        with self._lock:
            have = {e.url for e in self._entries}
            for raw in proxies:
                proxy = _normalize(raw, self.scheme)
                if proxy and proxy not in have:
                    self._entries.append(_Entry(url=proxy))
                    have.add(proxy)
                    added += 1
        return added

    # ----- validation ------------------------------------------------------

    def validate(
        self,
        test_url: str = DEFAULT_TEST_URL,
        timeout: float = 8.0,
        max_workers: int = 40,
        limit: Optional[int] = 400,
    ) -> int:
        """Health-check proxies concurrently; keep only responsive ones.

        Tests at most ``limit`` proxies (free lists can hold thousands) using a
        pool of ``max_workers`` threads. Returns the number of proxies kept.
        """
        with self._lock:
            candidates = [e.url for e in self._entries]
        if limit is not None:
            candidates = candidates[:limit]
        if not candidates:
            return 0

        alive: list[str] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._check, url, test_url, timeout): url
                for url in candidates
            }
            for fut in as_completed(futures):
                if fut.result():
                    alive.append(futures[fut])

        with self._lock:
            self._entries = [_Entry(url=u) for u in alive]
            self._index = 0
        logger.info("proxy validation: %d/%d alive", len(alive), len(candidates))
        return len(alive)

    def _check(self, proxy: str, test_url: str, timeout: float) -> bool:
        """Return True if a request through ``proxy`` succeeds quickly."""
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(handler)
        opener.addheaders = [("User-Agent", _FETCH_UA)]
        try:
            with opener.open(test_url, timeout=timeout) as resp:
                return resp.status in (200, 204)
        except Exception:  # noqa: BLE001 - any failure means "not usable"
            return False

    # ----- rotation --------------------------------------------------------

    def get(self) -> Optional[str]:
        """Return the next usable proxy (round-robin), or ``None`` if none.

        Skips entries whose cooldown has not elapsed. When every entry is on
        cooldown, returns ``None`` so the caller connects directly.
        """
        now = time.time()
        with self._lock:
            n = len(self._entries)
            if n == 0:
                return None
            for _ in range(n):
                entry = self._entries[self._index % n]
                self._index = (self._index + 1) % n
                if entry.cooldown_until <= now:
                    return entry.url
        return None

    def mark_bad(self, proxy: str) -> None:
        """Record a failure for ``proxy``: cool it down or drop it entirely."""
        if not proxy:
            return
        with self._lock:
            for i, entry in enumerate(self._entries):
                if entry.url != proxy:
                    continue
                entry.fails += 1
                if entry.fails >= self.max_fails:
                    del self._entries[i]
                    # Keep the round-robin cursor pointing at the same logical
                    # "next" entry: deleting an item before the cursor shifts
                    # everything after it left by one, so decrement to match.
                    if i < self._index:
                        self._index -= 1
                    logger.debug("dropped dead proxy %s", proxy)
                else:
                    entry.cooldown_until = time.time() + self.cooldown
                    logger.debug("cooled down proxy %s (fails=%d)", proxy, entry.fails)
                return

    # ----- introspection ---------------------------------------------------

    @property
    def size(self) -> int:
        """Total proxies currently in the pool."""
        with self._lock:
            return len(self._entries)

    @property
    def available(self) -> int:
        """Proxies not currently on cooldown."""
        now = time.time()
        with self._lock:
            return sum(1 for e in self._entries if e.cooldown_until <= now)


def _parse_sources(text: str) -> list[str]:
    """Split a user-supplied source blob (newline/comma separated) into URLs."""
    if not text:
        return []
    parts = re.split(r"[\n,]+", text)
    return [p.strip() for p in parts if p.strip()]


def build_pool_from_settings(settings: "Settings") -> Optional[ProxyPool]:
    """Build and prime a :class:`ProxyPool` from settings, or ``None`` if off.

    Returns ``None`` unless both ``proxy_enabled`` and ``proxy_rotate`` are set.
    Custom ``proxy_sources`` (paid or private lists) override the built-in free
    sources. Performs the network fetch (and optional validation) inline, so run
    this off the GUI thread when possible.
    """
    if not (settings.proxy_enabled and settings.proxy_rotate):
        return None

    custom = _parse_sources(settings.proxy_sources)
    pool = ProxyPool(sources=custom or None)
    count = pool.refresh()
    if count == 0:
        logger.warning("proxy rotation enabled but no proxies were fetched; "
                       "downloads will fall back to a direct connection")
        return pool
    if settings.proxy_validate:
        pool.validate()
    return pool
