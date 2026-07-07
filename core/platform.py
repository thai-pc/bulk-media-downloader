"""URL to :class:`Platform` detection.

Detection is host-based, case-insensitive, and matches on the registered domain
after stripping the ``www.`` / ``m.`` / ``mobile.`` subdomain prefixes. See
SPEC section 4.2.
"""

from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse


class Platform(str, Enum):
    """Supported media platforms.

    Subclassing ``str`` lets the value be used directly as a dict key, in logs,
    and in the checkpoint JSON without conversion.
    """

    YOUTUBE = "youtube"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    TWITTER = "twitter"  # X / Twitter
    UNKNOWN = "unknown"


# Host suffix patterns per platform. A host matches when it equals a pattern or
# ends with ("." + pattern).
_DOMAIN_PATTERNS: dict[Platform, tuple[str, ...]] = {
    Platform.YOUTUBE: ("youtube.com", "youtu.be", "youtube-nocookie.com"),
    Platform.FACEBOOK: ("facebook.com", "fb.com", "fb.watch"),
    Platform.INSTAGRAM: ("instagram.com", "instagr.am"),
    Platform.TIKTOK: ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com"),
    Platform.TWITTER: ("twitter.com", "x.com", "t.co"),
}

# Subdomain prefixes stripped from the netloc before matching.
_STRIP_PREFIXES = ("www.", "m.", "mobile.")


def normalize_url(url: str) -> str:
    """Trim whitespace and prepend ``https://`` when a scheme is missing.

    Tracking parameters are intentionally *not* stripped so the same link hashes
    identically across runs (the checkpoint done-key depends on this).
    """
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
    return url


def _host_of(url: str) -> str:
    """Return the lowercased host with subdomain prefixes and port removed."""
    parsed = urlparse(normalize_url(url))
    host = parsed.netloc.lower()
    # Drop credentials (user:pass@) if present.
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    # Drop the port.
    if ":" in host:
        host = host.split(":", 1)[0]
    for prefix in _STRIP_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    return host


def detect_platform(url: str) -> Platform:
    """Return the :class:`Platform` for ``url`` or :attr:`Platform.UNKNOWN`."""
    host = _host_of(url)
    if not host:
        return Platform.UNKNOWN
    for platform, patterns in _DOMAIN_PATTERNS.items():
        for pattern in patterns:
            if host == pattern or host.endswith("." + pattern):
                return platform
    return Platform.UNKNOWN


def is_supported(url: str) -> bool:
    """Return ``True`` unless the URL maps to :attr:`Platform.UNKNOWN`."""
    return detect_platform(url) is not Platform.UNKNOWN
