"""Engine wrapper around yt-dlp and gallery-dl (SPEC section 4.3).

The downloader is Qt-free and reports progress through a plain callable. It
performs exactly *one* attempt per :meth:`Downloader.download` call; the retry
loop lives in :class:`core.anti_block.AntiBlock`. Missing engines produce clear,
actionable errors rather than opaque crashes.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from core.anti_block import CancelledError
from core.platform import Platform
# Job/JobStatus imported at runtime; queue_manager imports our types only under
# TYPE_CHECKING, so there is no import cycle.
from core.queue_manager import Job, JobStatus

if TYPE_CHECKING:
    from core.anti_block import AntiBlock
    from core.config import Settings

logger = logging.getLogger(__name__)

# Platforms whose primary engine may fail on image-only content and fall back.
_MIXED_PLATFORMS = {Platform.INSTAGRAM, Platform.TWITTER}

# Platforms that always use yt-dlp.
_VIDEO_PLATFORMS = {Platform.YOUTUBE, Platform.TIKTOK, Platform.FACEBOOK}

# yt-dlp quality preset -> format string (SPEC 4.3).
_FORMAT_MAP = {
    "best": "bestvideo*+bestaudio/best",
    "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480": "bestvideo[height<=480]+bestaudio/best[height<=480]",
    "audio": "bestaudio/best",
}


@dataclass
class ProgressEvent:
    """A progress update pushed to the caller-supplied callback."""

    job_id: int
    status: JobStatus            # RUNNING / DONE / FAILED
    progress: float              # 0..100 (best-effort)
    title: str = ""
    speed: str = ""
    eta: str = ""
    output_path: str = ""
    error: str = ""


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass
class DownloadResult:
    """Outcome of a single download attempt."""

    ok: bool
    output_path: str = ""
    title: str = ""
    error: str = ""
    retryable: bool = False  # True for rate-limit / transient errors


class Downloader:
    """Routes a :class:`Job` to yt-dlp or gallery-dl and reports progress."""

    def __init__(self, settings: "Settings", anti_block: "AntiBlock") -> None:
        self.settings = settings
        self.anti_block = anti_block
        self._ffmpeg_warned = False

    # ----- engine selection ------------------------------------------------

    def select_engine(self, platform: Platform) -> str:
        """Return the primary engine id ("ytdlp" | "gallery_dl") for a platform.

        ``prefer_gallery_dl_for`` is read defensively via ``getattr`` because it
        is mentioned in SPEC 4.3 but not part of the :class:`Settings` table;
        this keeps the hook available without breaking the dataclass contract.
        """
        prefer = getattr(self.settings, "prefer_gallery_dl_for", []) or []
        if platform in prefer or platform.value in prefer:
            return "gallery_dl"
        if platform in _VIDEO_PLATFORMS or platform in _MIXED_PLATFORMS:
            return "ytdlp"
        return "ytdlp"

    def _is_retryable(self, error_class, message: str) -> bool:
        """Decide whether a failed attempt should be retried.

        Normally only RETRYABLE errors are retried. But when proxy rotation is
        active, an IP-flag / bot-challenge (which yt-dlp reports as an auth-style
        "Sign in to confirm you're not a bot") means *this proxy's IP* is flagged
        — retrying rotates to a different IP, so we treat it as retryable too.
        """
        if error_class.value == "retryable":
            return True
        if self.anti_block.proxy_pool is not None and _is_ip_flag(message):
            return True
        return False

    # ----- public download entry ------------------------------------------

    def download(
        self,
        job: Job,
        on_progress: ProgressCallback,
        cancel_check: Callable[[], bool] = lambda: False,
    ) -> DownloadResult:
        """Perform one download attempt for ``job``.

        For the mixed platforms (Instagram, X) a yt-dlp "no video / unsupported"
        failure falls back to gallery-dl within this same attempt. Cancellation
        propagates as :class:`CancelledError`; the queue turns that into
        ``CANCELLED``. Any other engine error is classified retryable/fatal.
        """
        engine = self.select_engine(job.platform)

        if engine == "gallery_dl":
            return self._download_gallery_dl(job, on_progress, cancel_check)

        # Primary engine is yt-dlp.
        try:
            return self._download_ytdlp(job, on_progress, cancel_check)
        except CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - classified below
            error_class = self.anti_block.classify_error(exc)
            message = _first_line(str(exc))
            no_video = _looks_like_no_video(str(exc))
            if job.platform in _MIXED_PLATFORMS and no_video:
                logger.info(
                    "job %d: yt-dlp found no video, falling back to gallery-dl",
                    job.id,
                )
                return self._download_gallery_dl(job, on_progress, cancel_check)
            retryable = self._is_retryable(error_class, str(exc))
            logger.error("job %d yt-dlp error (%s): %s", job.id, error_class.value, message)
            return DownloadResult(ok=False, error=_auth_message(error_class, message),
                                  retryable=retryable)

    # ----- yt-dlp ----------------------------------------------------------

    def _download_ytdlp(
        self,
        job: Job,
        on_progress: ProgressCallback,
        cancel_check: Callable[[], bool],
    ) -> DownloadResult:
        """Download a video/audio job with the yt-dlp Python API."""
        try:
            import yt_dlp  # local import so the module is optional
        except ImportError as exc:
            raise RuntimeError(
                "yt-dlp is not installed. Install it with 'pip install yt-dlp' "
                "(or 'pip install -r requirements.txt')."
            ) from exc

        outtmpl = os.path.join(
            self.settings.output_dir,
            job.platform.value,
            "%(uploader)s",
            "%(title).120B [%(id)s].%(ext)s",
        )
        os.makedirs(os.path.dirname(os.path.dirname(outtmpl)), exist_ok=True)

        state = {"output_path": "", "title": ""}

        def hook(d: dict) -> None:
            if cancel_check():
                raise CancelledError("cancelled by user")
            status = d.get("status")
            info = d.get("info_dict") or {}
            title = info.get("title") or state["title"]
            state["title"] = title
            if status == "downloading":
                progress = _percent_from_hook(d)
                on_progress(ProgressEvent(
                    job_id=job.id, status=JobStatus.RUNNING, progress=progress,
                    title=title, speed=_fmt_speed(d.get("speed")),
                    eta=_fmt_eta(d.get("eta")),
                ))
            elif status == "finished":
                filename = d.get("filename") or ""
                if filename:
                    state["output_path"] = filename
                on_progress(ProgressEvent(
                    job_id=job.id, status=JobStatus.RUNNING, progress=99.0,
                    title=title, output_path=state["output_path"],
                ))

        options = self._build_ytdlp_options(outtmpl, hook)

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(job.url, download=True)
            if info is None:
                raise RuntimeError("yt-dlp returned no media information")
            title = info.get("title", "") or state["title"]
            # Resolve the final output path from yt-dlp's own machinery.
            output_path = state["output_path"]
            try:
                output_path = ydl.prepare_filename(info)
            except Exception:  # noqa: BLE001 - best-effort path resolution
                pass

        on_progress(ProgressEvent(
            job_id=job.id, status=JobStatus.DONE, progress=100.0,
            title=title, output_path=output_path,
        ))
        return DownloadResult(ok=True, output_path=output_path, title=title)

    def _build_ytdlp_options(self, outtmpl: str, hook: Callable) -> dict:
        """Assemble the yt-dlp options dict from settings + anti-block layer."""
        quality = self.settings.quality
        fmt = _FORMAT_MAP.get(quality, _FORMAT_MAP["best"])

        have_ffmpeg = shutil.which("ffmpeg") is not None
        if not have_ffmpeg and not self._ffmpeg_warned:
            logger.warning(
                "ffmpeg not found on PATH: falling back to a single progressive "
                "format; video+audio merging and mp3 extraction are unavailable."
            )
            self._ffmpeg_warned = True

        options: dict = {
            "outtmpl": outtmpl,
            "format": fmt,
            "noplaylist": False,
            "retries": 0,               # retry handled by our anti_block layer
            "fragment_retries": 0,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "windowsfilenames": True,
            "restrictfilenames": False,
            "progress_hooks": [hook],
            "http_headers": self.anti_block.http_headers(),
        }

        if not have_ffmpeg:
            # Progressive single-file fallback so no merge step is needed.
            options["format"] = "best[ext=mp4]/best"

        if quality == "audio" and have_ffmpeg:
            options["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]

        options.update(self.anti_block.cookies_args())

        proxy = self.anti_block.proxy()
        if proxy:
            options["proxy"] = proxy

        rate = self.settings.rate_limit_bps()
        if rate:
            options["ratelimit"] = rate

        return options

    # ----- gallery-dl ------------------------------------------------------

    def _download_gallery_dl(
        self,
        job: Job,
        on_progress: ProgressCallback,
        cancel_check: Callable[[], bool],
    ) -> DownloadResult:
        """Download an image/album job via the gallery-dl CLI subprocess."""
        if importlib.util.find_spec("gallery_dl") is None:
            return DownloadResult(
                ok=False,
                error=("gallery-dl is not installed. Install it with "
                       "'pip install gallery-dl'."),
                retryable=False,
            )

        dest_dir = os.path.join(self.settings.output_dir, job.platform.value)
        os.makedirs(dest_dir, exist_ok=True)

        on_progress(ProgressEvent(
            job_id=job.id, status=JobStatus.RUNNING, progress=0.0,
        ))

        cmd = [sys.executable, "-m", "gallery_dl", "-D", dest_dir]

        # Windows-safe filename template (SPEC 4.3, sanitized).
        cmd += ["-f", "{category}_{id}_{num}.{extension}"]

        if self.settings.cookies_file:
            cmd += ["--cookies", self.settings.cookies_file]
        elif self.settings.cookies_from_browser:
            cmd += ["--cookies-from-browser", self.settings.cookies_from_browser]

        proxy = self.anti_block.proxy()
        if proxy:
            cmd += ["--proxy", proxy]

        # A per-request sleep gives gallery-dl its own gentle pacing.
        cmd += ["--sleep", f"{self.settings.delay_min}-{self.settings.delay_max}"]

        rate = self.settings.rate_limit_bps()
        if rate:
            cmd += ["--limit-rate", str(rate)]

        # A rotated UA passed through gallery-dl's config option.
        ua = self.anti_block.next_user_agent()
        cmd += ["-o", f"user-agent={ua}"]

        cmd.append(job.url)

        if cancel_check():
            raise CancelledError("cancelled by user")

        logger.debug("gallery-dl cmd: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=3600,
            )
        except subprocess.TimeoutExpired:
            return DownloadResult(ok=False, error="gallery-dl timed out",
                                  retryable=True)

        if cancel_check():
            raise CancelledError("cancelled by user")

        if proc.returncode == 0:
            output_path = _last_output_path(proc.stdout) or dest_dir
            on_progress(ProgressEvent(
                job_id=job.id, status=JobStatus.DONE, progress=100.0,
                output_path=output_path,
            ))
            return DownloadResult(ok=True, output_path=output_path)

        stderr = (proc.stderr or proc.stdout or "").strip()
        message = _first_line(stderr) or f"gallery-dl exited with {proc.returncode}"
        error_class = self.anti_block.classify_error(RuntimeError(stderr))
        retryable = self._is_retryable(error_class, stderr)
        logger.error("job %d gallery-dl error (%s): %s", job.id, error_class.value, message)
        return DownloadResult(ok=False, error=_auth_message(error_class, message),
                              retryable=retryable)


# ----- module helpers ------------------------------------------------------

def _percent_from_hook(d: dict) -> float:
    """Extract a 0..100 percentage from a yt-dlp progress-hook dict."""
    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    downloaded = d.get("downloaded_bytes")
    if total and downloaded:
        return max(0.0, min(100.0, downloaded / total * 100.0))
    text = d.get("_percent_str", "")
    match = re.search(r"(\d+(?:\.\d+)?)%", text or "")
    return float(match.group(1)) if match else 0.0


def _fmt_speed(speed: Optional[float]) -> str:
    """Format a bytes/sec speed like '1.2MiB/s'."""
    if not speed:
        return ""
    units = ["B/s", "KiB/s", "MiB/s", "GiB/s"]
    value = float(speed)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}"
        value /= 1024
    return ""


def _fmt_eta(eta: Optional[float]) -> str:
    """Format an ETA in seconds like '00:12'."""
    if eta is None:
        return ""
    try:
        seconds = int(eta)
    except (TypeError, ValueError):
        return ""
    minutes, secs = divmod(max(0, seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def _first_line(text: str) -> str:
    """Return the first non-empty line of ``text``, trimmed."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return (text or "").strip()


def _is_ip_flag(message: str) -> bool:
    """Return True when an error signals the current IP is flagged/challenged.

    These are worth retrying *only when rotating proxies* — a fresh IP may pass.
    Covers both straight and curly apostrophes in yt-dlp's bot-challenge text.
    """
    lowered = message.lower()
    return any(marker in lowered for marker in (
        "confirm you're not a bot", "confirm you’re not a bot",
        "sign in to confirm", "not a bot", "unusual traffic",
        "verify you are human", "captcha", "rate-limit reached",
    ))


def _looks_like_no_video(message: str) -> bool:
    """Return True when a yt-dlp error signals image-only/unsupported content."""
    lowered = message.lower()
    return any(marker in lowered for marker in (
        "there is no video", "no video", "unsupported url",
    ))


def _auth_message(error_class, message: str) -> str:
    """Prefix authentication errors with a distinct, actionable hint."""
    if getattr(error_class, "value", "") == "auth":
        return f"Login/cookies required: {message}"
    return message


def _last_output_path(stdout: str) -> str:
    """Best-effort extraction of the last downloaded path from gallery-dl output.

    gallery-dl prints one downloaded file path per line (optionally prefixed
    with a marker). We take the last existing path-looking line.
    """
    last = ""
    for line in (stdout or "").splitlines():
        line = line.strip().lstrip("# ").strip()
        if not line:
            continue
        # gallery-dl paths usually contain a separator.
        if os.sep in line or "/" in line:
            last = line
    return last
