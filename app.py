"""Bulk Media Downloader — entry point (SPEC section 4.1).

Runs the GUI by default. If any CLI download flag is present (``--input``,
``--url``, positional URLs, or ``--cli``) it runs headless. The GUI import is
guarded so CLI mode works even when PySide6 is not installed.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

from core.config import Settings
from core.queue_manager import JobStatus

__version__ = "1.0.0"

logger = logging.getLogger("bmd")

LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for all CLI flags (SPEC 4.1)."""
    parser = argparse.ArgumentParser(
        prog="app.py",
        description="Bulk download media from YouTube/Facebook/Instagram/"
                    "TikTok/X using yt-dlp and gallery-dl.",
    )
    parser.add_argument("urls", nargs="*", help="URLs to download (positional).")
    parser.add_argument("-i", "--input", default=None,
                        help="Text/CSV file, one URL per line (# comments ok).")
    parser.add_argument("-u", "--url", action="append", default=[],
                        help="A URL to download (repeatable).")
    parser.add_argument("-o", "--output", default=None,
                        help="Output root folder (default ./downloads).")
    parser.add_argument("-t", "--threads", type=int, default=None,
                        help="Total worker threads (1-16, default 4).")
    parser.add_argument("--per-platform", type=int, default=None,
                        dest="per_platform",
                        help="Max concurrent jobs per platform (1-8, default 2).")
    parser.add_argument("--delay-min", type=float, default=None,
                        dest="delay_min", help="Min pre-download delay (s).")
    parser.add_argument("--delay-max", type=float, default=None,
                        dest="delay_max", help="Max pre-download delay (s).")
    parser.add_argument("--retries", type=int, default=None,
                        help="Max retry attempts (0-10, default 4).")
    parser.add_argument("--cookies", default=None,
                        help="Cookies file (Netscape format).")
    parser.add_argument("--cookies-from-browser", default=None,
                        dest="cookies_from_browser",
                        help="Browser for yt-dlp cookies (chrome/firefox/edge).")
    parser.add_argument("--quality", default=None,
                        choices=["best", "1080", "720", "480", "audio"],
                        help="Quality preset (default best).")
    parser.add_argument("--proxy", default=None,
                        help="A single proxy URL, e.g. http://ip:port (no rotation).")
    parser.add_argument("--proxy-rotate", action="store_true", dest="proxy_rotate",
                        help="Rotate through a pool of free proxies (see --proxy-source).")
    parser.add_argument("--proxy-source", action="append", default=[],
                        dest="proxy_source",
                        help="Custom proxy-list URL (repeatable); overrides built-in "
                             "free sources. Each returns ip:port or scheme://ip:port lines.")
    parser.add_argument("--no-proxy-validate", action="store_true",
                        dest="no_proxy_validate",
                        help="Skip health-checking proxies before use (faster, less reliable).")
    parser.add_argument("--checkpoint", default=None,
                        help="Checkpoint file location.")
    parser.add_argument("--no-resume", action="store_true", dest="no_resume",
                        help="Ignore the checkpoint (re-download everything).")
    parser.add_argument("--log-file", default=None, dest="log_file",
                        help="Log file location.")
    parser.add_argument("--log-level", default=None, dest="log_level",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging verbosity (default INFO).")
    parser.add_argument("--cli", action="store_true",
                        help="Force CLI mode (reads stdin if no URLs).")
    parser.add_argument("--gui", action="store_true",
                        help="Force GUI mode even with input flags.")
    parser.add_argument("--version", action="version",
                        version=f"Bulk Media Downloader {__version__}")
    return parser


def _has_input_flags(args: argparse.Namespace) -> bool:
    """Return True if any flag that triggers CLI mode is present."""
    return bool(args.input or args.url or args.urls or args.cli)


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

def setup_logging(settings: Settings, cli_mode: bool) -> None:
    """Configure root logging with a rotating file handler + stream handler."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT)

    log_path = settings.effective_log_file()
    try:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:  # pragma: no cover - unusual FS failure
        print(f"warning: could not open log file {log_path}: {exc}",
              file=sys.stderr)

    stream = logging.StreamHandler(sys.stdout if cli_mode else sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)


# --------------------------------------------------------------------------
# URL collection
# --------------------------------------------------------------------------

def _read_url_file(path: str) -> list[str]:
    """Read URLs from a text/CSV file (# comments and blanks ignored)."""
    urls: list[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line.split(",")[0].strip())
    return urls


def _collect_urls(args: argparse.Namespace) -> list[str]:
    """Gather URLs from --input, --url, positional, and stdin; dedupe in order."""
    collected: list[str] = []
    if args.input:
        collected += _read_url_file(args.input)
    collected += list(args.url or [])
    collected += list(args.urls or [])
    if args.cli and not collected and not sys.stdin.isatty():
        collected += [ln.strip() for ln in sys.stdin
                      if ln.strip() and not ln.strip().startswith("#")]
    seen: set[str] = set()
    ordered: list[str] = []
    for url in collected:
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


# --------------------------------------------------------------------------
# CLI mode
# --------------------------------------------------------------------------

def run_cli(args: argparse.Namespace) -> int:
    """Run a headless download batch and return a process exit code."""
    base = Settings()
    settings = Settings.from_args(args, base=base)
    setup_logging(settings, cli_mode=True)

    urls = _collect_urls(args)
    if not urls:
        print("No URLs provided. Use --input, --url, or positional URLs.",
              file=sys.stderr)
        return 2

    os.makedirs(settings.output_dir, exist_ok=True)

    # Import core collaborators lazily so --help never needs them.
    from core.anti_block import AntiBlock
    from core.checkpoint import CheckpointStore
    from core.downloader import Downloader
    from core.proxy_pool import build_pool_from_settings
    from core.queue_manager import QueueManager

    proxy_pool = build_pool_from_settings(settings)
    if proxy_pool is not None:
        print(f"Proxy rotation: {proxy_pool.available} usable proxy(ies) in pool.",
              flush=True)
    anti_block = AntiBlock(settings, proxy_pool=proxy_pool)
    downloader = Downloader(settings, anti_block)
    checkpoint = CheckpointStore(settings.effective_checkpoint_path())
    checkpoint.load()

    queue = QueueManager(settings, downloader, anti_block, checkpoint)
    queue.add_urls(urls)

    last_print: dict[int, float] = {}

    def printer(job) -> None:
        # Throttle running updates to ~1/sec per job; always print terminals.
        now = time.time()
        terminal = job.status in (
            JobStatus.DONE, JobStatus.FAILED, JobStatus.SKIPPED,
            JobStatus.CANCELLED)
        if not terminal and now - last_print.get(job.id, 0.0) < 1.0:
            return
        last_print[job.id] = now
        title = (job.title or job.url)[:60]
        line = (f"[{job.id:>3}] {job.platform.value:<9} "
                f"{job.status.value:<9} {job.progress:5.1f}%  {title}")
        if job.error:
            line += f"  ERR: {job.error[:80]}"
        print(line, flush=True)

    queue.on_job_event(printer)

    print(f"Starting {len(urls)} job(s) -> {settings.output_dir}", flush=True)
    queue.start()
    queue.wait()

    summary = queue.summary()
    print("-" * 60)
    print(f"Total: {summary.total} | Done: {summary.done} | "
          f"Failed: {summary.failed} | Skipped: {summary.skipped}", flush=True)
    return 0 if summary.failed == 0 else 1


# --------------------------------------------------------------------------
# GUI mode
# --------------------------------------------------------------------------

def run_gui(settings: Settings) -> int:
    """Launch the PySide6 GUI. Returns the Qt exit code."""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "GUI mode requires PySide6, which is not installed.\n"
            "Install it with 'pip install PySide6' (or "
            "'pip install -r requirements.txt'),\n"
            "or use CLI mode, e.g.: python app.py --input links.txt "
            "--output ./downloads",
            file=sys.stderr,
        )
        return 2

    from ui.main_window import MainWindow

    setup_logging(settings, cli_mode=False)

    app = QApplication(sys.argv)
    window = MainWindow(settings)
    window.show()
    return app.exec()


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to GUI or CLI. Returns the exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    force_gui = args.gui
    cli_requested = _has_input_flags(args) and not force_gui

    if cli_requested:
        return run_cli(args)

    # GUI mode. Load persisted settings as the base, then merge any provided
    # flags over them (CLI flags override saved settings, per SPEC 4.1).
    base = Settings().load_from_qsettings()
    settings = Settings.from_args(args, base=base)
    return run_gui(settings)


if __name__ == "__main__":
    sys.exit(main())
