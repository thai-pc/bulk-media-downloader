"""Application settings — the single source of truth shared by CLI and GUI.

The :class:`Settings` dataclass is defined here (SPEC section 5). Persistence
uses ``QSettings`` but the import is deferred into the two methods that need it
so this module stays importable in a headless / no-PySide6 environment. This is
the one intentional deviation from "core is Qt-free": the Qt symbol is never
touched at import time, only when the GUI actually calls load/save.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Any, Optional

QSETTINGS_ORG = "BMD"
QSETTINGS_APP = "BulkMediaDownloader"

QUALITY_CHOICES = ("best", "1080", "720", "480", "audio")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
BROWSER_CHOICES = ("", "chrome", "firefox", "edge")


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


@dataclass
class Settings:
    """All user-configurable options with their spec defaults and ranges."""

    output_dir: str = "./downloads"
    threads: int = 4                      # 1-16
    per_platform: int = 2                 # 1-8
    delay_min: float = 1.0                # 0-60
    delay_max: float = 5.0                # >= delay_min, <= 60
    retries: int = 4                      # 0-10
    cookies_file: str = ""
    cookies_from_browser: str = ""        # ""|chrome|firefox|edge
    quality: str = "best"                 # best|1080|720|480|audio
    rate_limit_kbps: int = 0              # 0 = unlimited
    proxy_enabled: bool = False
    proxy: str = ""
    proxy_rotate: bool = False            # rotate through a pool of proxies
    proxy_sources: str = ""               # custom source URLs (newline/comma); "" => built-in free lists
    proxy_validate: bool = True           # health-check proxies before use
    resume: bool = True                   # False == --no-resume
    checkpoint_path: str = ""             # "" => <output_dir>/.bmd_checkpoint.json
    log_file: str = ""                    # "" => <output_dir>/bmd.log
    log_level: str = "INFO"

    # ----- derived helpers -------------------------------------------------

    def effective_checkpoint_path(self) -> str:
        """Return the checkpoint path, deriving it from ``output_dir`` if unset."""
        if self.checkpoint_path:
            return self.checkpoint_path
        return os.path.join(self.output_dir, ".bmd_checkpoint.json")

    def effective_log_file(self) -> str:
        """Return the log file path, deriving it from ``output_dir`` if unset."""
        if self.log_file:
            return self.log_file
        return os.path.join(self.output_dir, "bmd.log")

    def rate_limit_bps(self) -> Optional[int]:
        """Return the byte/sec rate limit for the engines, or ``None`` if off."""
        if self.rate_limit_kbps and self.rate_limit_kbps > 0:
            return int(self.rate_limit_kbps * 1024)
        return None

    def validate(self) -> "Settings":
        """Clamp numeric fields to their allowed ranges and normalize choices.

        Returns ``self`` so calls can be chained. Mutates in place.
        """
        self.threads = int(_clamp(self.threads, 1, 16))
        self.per_platform = int(_clamp(self.per_platform, 1, 8))
        self.delay_min = float(_clamp(self.delay_min, 0.0, 60.0))
        self.delay_max = float(_clamp(self.delay_max, 0.0, 60.0))
        if self.delay_max < self.delay_min:
            # Keep the invariant delay_max >= delay_min by swapping.
            self.delay_min, self.delay_max = self.delay_max, self.delay_min
        self.retries = int(_clamp(self.retries, 0, 10))
        if self.quality not in QUALITY_CHOICES:
            self.quality = "best"
        if self.log_level not in LOG_LEVELS:
            self.log_level = "INFO"
        if self.cookies_from_browser not in BROWSER_CHOICES:
            self.cookies_from_browser = ""
        if self.rate_limit_kbps < 0:
            self.rate_limit_kbps = 0
        # cookies_from_browser and cookies_file are mutually exclusive; the file
        # takes precedence when both are set.
        if self.cookies_file and self.cookies_from_browser:
            self.cookies_from_browser = ""
        return self

    # ----- QSettings persistence (lazy Qt import) --------------------------

    def load_from_qsettings(self) -> "Settings":
        """Load persisted values from ``QSettings`` into this instance.

        Missing keys keep the current (default) value. Requires PySide6; the
        import is deferred so this module stays headless-importable.
        """
        try:
            from PySide6.QtCore import QSettings  # local import: keep core Qt-free
        except ImportError:  # pragma: no cover - GUI-only path
            return self
        qs = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        for f in fields(self):
            if not qs.contains(f.name):
                continue
            raw = qs.value(f.name)
            setattr(self, f.name, _coerce(raw, getattr(self, f.name)))
        return self.validate()

    def save_to_qsettings(self) -> None:
        """Persist all fields to ``QSettings``. Requires PySide6."""
        try:
            from PySide6.QtCore import QSettings  # local import: keep core Qt-free
        except ImportError:  # pragma: no cover - GUI-only path
            return
        qs = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        for f in fields(self):
            qs.setValue(f.name, getattr(self, f.name))
        qs.sync()

    # ----- construction from argparse --------------------------------------

    @classmethod
    def from_args(cls, args: Any, base: "Settings | None" = None) -> "Settings":
        """Build a :class:`Settings` by layering argparse values over ``base``.

        ``base`` is typically the persisted settings; any CLI flag that was
        explicitly supplied overrides it. Flags left at their argparse default
        of ``None`` do not override the base value.
        """
        settings = base if base is not None else cls()

        def take(attr: str, dest: str) -> None:
            value = getattr(args, attr, None)
            if value is not None:
                setattr(settings, dest, value)

        take("output", "output_dir")
        take("threads", "threads")
        take("per_platform", "per_platform")
        take("delay_min", "delay_min")
        take("delay_max", "delay_max")
        take("retries", "retries")
        take("cookies", "cookies_file")
        take("cookies_from_browser", "cookies_from_browser")
        take("quality", "quality")
        take("proxy", "proxy")
        take("checkpoint", "checkpoint_path")
        take("log_file", "log_file")
        take("log_level", "log_level")

        # --no-resume is a store_true flag; only flip when explicitly set.
        if getattr(args, "no_resume", False):
            settings.resume = False
        if getattr(args, "proxy", None):
            settings.proxy_enabled = True
        # --proxy-rotate turns on rotation (and implies proxy is enabled).
        if getattr(args, "proxy_rotate", False):
            settings.proxy_enabled = True
            settings.proxy_rotate = True
        # --proxy-source is repeatable; join into the newline-separated field.
        sources = getattr(args, "proxy_source", None)
        if sources:
            settings.proxy_sources = "\n".join(sources)
        if getattr(args, "no_proxy_validate", False):
            settings.proxy_validate = False

        return settings.validate()


def _coerce(raw: Any, template: Any) -> Any:
    """Coerce a ``QSettings`` value (often a string) to ``template``'s type."""
    if isinstance(template, bool):
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if isinstance(template, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return template
    if isinstance(template, float):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return template
    return str(raw)


def load_settings() -> Settings:
    """Convenience: return persisted settings (or defaults when no PySide6)."""
    return Settings().load_from_qsettings()
