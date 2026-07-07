"""Anti-blocking layer — the graded core (SPEC section 4.4).

Centralizes every anti-blocking technique: random pre-download delay,
exponential-backoff retry, User-Agent rotation, cookie plumbing, a proxy stub,
and error classification. This module is Qt-free and network-free (the UA list
is static and embedded).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from enum import Enum
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:  # avoid import cycles / heavy imports at runtime
    from core.config import Settings
    from core.downloader import DownloadResult
    from core.proxy_pool import ProxyPool
    from core.queue_manager import Job

logger = logging.getLogger(__name__)

# Backoff constants (SPEC section 5). Not user-facing.
BACKOFF_BASE = 2.0
BACKOFF_FACTOR = 2.0
BACKOFF_CAP = 60.0
BACKOFF_JITTER = 0.25
RETRYABLE_HTTP = {429, 403, 408, 500, 502, 503, 504}

# Static desktop User-Agent pool. Last updated: 2026-01.
# Refresh periodically to track current Chrome/Firefox/Edge releases.
USER_AGENTS: list[str] = [
    # Chrome / Windows 10-11
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Edge / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    # Firefox / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) "
    "Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) "
    "Gecko/20100101 Firefox/122.0",
    # Chrome / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    # Safari / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    # Firefox / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:123.0) "
    "Gecko/20100101 Firefox/123.0",
]


class ErrorClass(str, Enum):
    """Classification of an engine/network error for retry decisions."""

    RETRYABLE = "retryable"
    FATAL = "fatal"
    AUTH = "auth"  # login/cookies required -> fatal but distinct message


class CancelledError(Exception):
    """Raised inside a download attempt when the user cancels the queue."""


# Substrings that mark a fatal, non-retryable engine error.
_FATAL_MARKERS = (
    "unsupported url",
    "is not a valid url",
    "no video",
    "there is no video",
    "private",
    "removed",
    "unavailable",
    "not found",
    "404",
    "deleted",
    "does not exist",
)

# Substrings that mark an authentication / login-required error.
_AUTH_MARKERS = (
    "login required",
    "log in",
    "sign in",
    "authentication",
    "cookies",
    "rate-limit reached",  # IG guest limit — but treated as auth-ish below
    "requires authentication",
    "401",
)

# Substrings that mark a transient / retryable error.
_RETRYABLE_MARKERS = (
    "429",
    "403",  # spec RETRYABLE_HTTP treats 403 as retryable (engines report it in the message)
    "too many requests",
    "temporarily",
    "timed out",
    "timeout",
    "connection reset",
    "connection aborted",
    "connection refused",
    "read timed out",
    "503",
    "502",
    "500",
    "408",
    "throttl",
    "try again",
    # Connection/proxy-level failures — common with free proxies; retrying
    # rotates to a different proxy. yt-dlp/gallery-dl wrap these in their own
    # error types, so we match on the message text.
    "ssl",
    "eof occurred",
    "violation of protocol",
    "tunnel connection failed",
    "cannot connect to proxy",
    "proxyerror",
    "unable to connect to proxy",
    "bad gateway",
    "remote end closed",
)


class AntiBlock:
    """Bundles the anti-blocking techniques used around each download attempt."""

    def __init__(
        self,
        settings: "Settings",
        proxy_pool: "Optional[ProxyPool]" = None,
    ) -> None:
        self.settings = settings
        self.proxy_pool = proxy_pool
        # Remembers the proxy handed to the current attempt, per worker thread,
        # so a retryable failure can cool down the exact proxy that was used.
        self._local = threading.local()

    # ----- delays & UA -----------------------------------------------------

    def pre_request_delay(self) -> None:
        """Sleep a random uniform delay in ``[delay_min, delay_max]`` seconds."""
        low = max(0.0, self.settings.delay_min)
        high = max(low, self.settings.delay_max)
        if high <= 0:
            return
        delay = random.uniform(low, high)
        logger.debug("pre-request delay %.2fs", delay)
        time.sleep(delay)

    def next_user_agent(self) -> str:
        """Return a random User-Agent from :data:`USER_AGENTS`."""
        return random.choice(USER_AGENTS)

    def backoff_wait(self, attempt: int) -> float:
        """Sleep and return the backoff duration for a 1-based ``attempt``.

        Wait = ``min(cap, base * factor**(attempt-1))`` with +/-25% jitter.
        """
        attempt = max(1, attempt)
        raw = BACKOFF_BASE * (BACKOFF_FACTOR ** (attempt - 1))
        capped = min(BACKOFF_CAP, raw)
        jitter = random.uniform(1.0 - BACKOFF_JITTER, 1.0 + BACKOFF_JITTER)
        wait = capped * jitter
        logger.debug("backoff attempt %d -> %.2fs", attempt, wait)
        time.sleep(wait)
        return wait

    # ----- error classification -------------------------------------------

    def classify_error(self, exc: BaseException) -> ErrorClass:
        """Map an engine/network exception to RETRYABLE / FATAL / AUTH."""
        # An explicit HTTP status attribute wins if present.
        status = getattr(exc, "status", None) or getattr(exc, "code", None)
        if isinstance(status, int):
            if status in RETRYABLE_HTTP and status != 403:
                return ErrorClass.RETRYABLE
            if status in (401,):
                return ErrorClass.AUTH
            if status == 403:
                # 403 is retryable per spec's RETRYABLE_HTTP set, but often
                # signals auth. Treat as retryable unless message says login.
                return ErrorClass.RETRYABLE

        message = str(exc).lower()

        # Auth markers take priority over the generic fatal markers.
        for marker in _AUTH_MARKERS:
            if marker in message:
                return ErrorClass.AUTH
        for marker in _RETRYABLE_MARKERS:
            if marker in message:
                return ErrorClass.RETRYABLE
        for marker in _FATAL_MARKERS:
            if marker in message:
                return ErrorClass.FATAL

        # Unknown network-ish errors default to retryable; anything else fatal.
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return ErrorClass.RETRYABLE
        return ErrorClass.FATAL

    # ----- engine plumbing -------------------------------------------------

    def http_headers(self) -> dict[str, str]:
        """Return request headers including a freshly rotated User-Agent."""
        return {
            "User-Agent": self.next_user_agent(),
            "Accept-Language": "en-US,en;q=0.9",
        }

    def cookies_args(self) -> dict:
        """Return yt-dlp cookie options: cookiefile or cookiesfrombrowser or {}."""
        if self.settings.cookies_file:
            return {"cookiefile": self.settings.cookies_file}
        if self.settings.cookies_from_browser:
            return {"cookiesfrombrowser": (self.settings.cookies_from_browser,)}
        return {}

    def proxy(self) -> Optional[str]:
        """Return the proxy for this attempt: rotated from the pool if present.

        When a :class:`~core.proxy_pool.ProxyPool` is attached, a fresh proxy is
        drawn per call (per attempt) and remembered on a thread-local so
        :meth:`report_proxy_failure` can cool down the exact one that failed.
        Falls back to the single configured proxy, then to a direct connection.
        """
        if self.proxy_pool is not None:
            chosen = self.proxy_pool.get()
            if chosen is not None:
                self._local.current = chosen
                return chosen
            # Pool attached but empty/all-cooled: fall through to the single
            # configured proxy (if any), then a direct connection. Clear the
            # thread-local so report_proxy_failure() never cools a non-pool proxy.
        self._local.current = None
        if self.settings.proxy_enabled and self.settings.proxy:
            return self.settings.proxy
        return None

    def report_proxy_failure(self) -> None:
        """Cool down the proxy used by the current thread's last attempt."""
        if self.proxy_pool is None:
            return
        current = getattr(self._local, "current", None)
        if current:
            self.proxy_pool.mark_bad(current)

    # ----- retry loop ------------------------------------------------------

    def run_with_retry(
        self,
        attempt_fn: Callable[[int], "DownloadResult"],
        job: "Job",
        cancel_check: Callable[[], bool] = lambda: False,
    ) -> "DownloadResult":
        """Run ``attempt_fn`` with pre-delay and exponential-backoff retries.

        Loops up to ``settings.retries + 1`` times. Before each attempt it waits
        the random pre-request delay; on a retryable failure it backs off and
        retries. Cancellation is honored between attempts.
        """
        # Imported lazily to avoid a circular import at module load time.
        from core.downloader import DownloadResult

        max_attempts = self.settings.retries + 1
        last_result: Optional[DownloadResult] = None

        for attempt in range(1, max_attempts + 1):
            if cancel_check():
                return DownloadResult(ok=False, error="Cancelled", retryable=False)

            self.pre_request_delay()

            if cancel_check():
                return DownloadResult(ok=False, error="Cancelled", retryable=False)

            job.attempts = attempt
            result = attempt_fn(attempt)
            last_result = result

            if result.ok or not result.retryable:
                return result

            # A retryable failure may be the proxy's fault: cool it down so the
            # next attempt rotates to a different IP.
            self.report_proxy_failure()

            if attempt < max_attempts:
                wait = self.backoff_wait(attempt)
                logger.warning(
                    "job %d attempt %d/%d failed (%s), backing off %.1fs",
                    job.id, attempt, max_attempts, result.error, wait,
                )

        return last_result if last_result is not None else DownloadResult(
            ok=False, error="No attempt executed", retryable=False
        )
