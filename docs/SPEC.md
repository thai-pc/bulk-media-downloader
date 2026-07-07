# SPEC: Bulk Media Downloader — Technical Specification

> Engineer-ready implementation spec for the solution agreed in `docs/SOLUTION.md`.
> This document is the source of truth for the coding phase. Where this spec and the
> solution doc disagree, **this spec wins**.

---

## 1. Overview & goals

**Purpose.** A Windows desktop application that downloads large volumes of media
(video and image albums) from **YouTube, Facebook, Instagram, TikTok, and X (Twitter)**
without being rate-limited or blocked by those platforms.

**Design principle.** Do **not** write custom crawlers. Wrap two mature engines —
**yt-dlp** (video) and **gallery-dl** (images/albums) — inside a custom layer that adds:
a job queue, a controlled multithreaded worker pool, an **anti-blocking layer** (the
graded core), checkpoint/resume, progress reporting, and both a **GUI** and a **CLI**.

**Goals**

- G1. Accept many URLs (paste or file), auto-detect platform, route to the right engine.
- G2. Download concurrently with per-platform concurrency caps.
- G3. Survive rate limiting via random delay, backoff-retry, User-Agent rotation, cookies.
- G4. Persist progress so an interrupted batch resumes and skips finished URLs.
- G5. Run identically from a GUI **and** from the command line.
- G6. Report per-job status and export a result log.

**Constraints**

- Python **3.11+**, GUI in **PySide6**, target OS **Windows** (must not hard-crash on
  macOS/Linux for dev, but Windows is the shipping target).
- Packageable to a single `.exe` via PyInstaller.
- Proxy support is **stubbed** now (interface defined), full rotation is a later extension.
- Buildable within a 2-day timeline — keep it pragmatic, no over-engineering.

---

## 2. Project layout

```
bulk-media-downloader/
├── app.py                 # Entry point: GUI mode (default) and CLI mode (argparse)
├── core/
│   ├── __init__.py
│   ├── platform.py        # URL → Platform enum detection
│   ├── downloader.py      # Wraps yt-dlp / gallery-dl, progress callback
│   ├── anti_block.py      # Delay, backoff-retry, UA rotation, cookies, proxy stub
│   ├── queue_manager.py   # Job queue + worker pool + per-platform caps + events
│   ├── checkpoint.py      # JSON state file, done-key = sha256(url)
│   └── config.py          # Settings load/save (QSettings-backed) + Settings dataclass
├── ui/
│   ├── __init__.py
│   ├── main_window.py     # Main window, jobs table, start/stop, signal wiring
│   └── settings_dialog.py # Configuration dialog
├── docs/
│   ├── SOLUTION.md
│   └── SPEC.md            # this file
├── requirements.txt
├── README.md
└── build.bat
```

> `core/config.py` is an addition to the planned structure; it centralizes the settings
> model so both `app.py` (CLI) and `ui/settings_dialog.py` (GUI) share one definition.

---

## 3. Data models

All data models live where they are most used but are importable without side effects.
Recommended home: `core/queue_manager.py` for `Job`/`JobStatus`, `core/platform.py` for
`Platform`, `core/config.py` for `Settings`.

### 3.1 `Platform` enum (`core/platform.py`)

```python
from enum import Enum

class Platform(str, Enum):
    YOUTUBE   = "youtube"
    FACEBOOK  = "facebook"
    INSTAGRAM = "instagram"
    TIKTOK    = "tiktok"
    TWITTER   = "twitter"   # X / Twitter
    UNKNOWN   = "unknown"
```

Subclassing `str` makes the value directly usable as a dict key, in logs, and in the
checkpoint file without conversion.

### 3.2 `JobStatus` enum (`core/queue_manager.py`)

```python
from enum import Enum

class JobStatus(str, Enum):
    QUEUED      = "queued"       # in queue, not yet started
    RUNNING     = "running"      # actively downloading
    DONE        = "done"         # completed successfully
    FAILED      = "failed"       # exhausted retries or fatal error
    SKIPPED     = "skipped"      # already in checkpoint (resume)
    CANCELLED   = "cancelled"    # user stopped the queue before it ran
```

### 3.3 `Job` dataclass (`core/queue_manager.py`)

```python
from dataclasses import dataclass, field
from typing import Optional
import time

@dataclass
class Job:
    id: int                          # sequential, assigned by QueueManager (1-based)
    url: str                         # source URL (as entered, trimmed)
    platform: Platform               # detected platform
    status: JobStatus = JobStatus.QUEUED
    title: str = ""                  # filled once the engine reports metadata
    progress: float = 0.0            # 0.0–100.0
    speed: str = ""                  # human string, e.g. "1.2MiB/s" (optional display)
    eta: str = ""                    # human string, e.g. "00:12" (optional display)
    output_path: str = ""            # final file/dir path once known
    error: str = ""                  # last error message when FAILED
    attempts: int = 0                # retry attempts consumed
    url_hash: str = ""               # sha256(url); computed at creation
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
```

`url_hash` is computed in `__post_init__` as `hashlib.sha256(url.encode()).hexdigest()`.

---

## 4. Module specifications

### 4.1 `app.py` — entry point (GUI + CLI)

**Behavior.** If any CLI download flag is present (e.g. `--input`, `--url`, or `--cli`),
run **headless CLI mode**. Otherwise launch the **GUI**.

```python
def main(argv: list[str] | None = None) -> int: ...
def run_gui(settings: Settings) -> int: ...
def run_cli(args: argparse.Namespace) -> int: ...
```

`main()` builds the argparse parser, parses args, merges them over persisted settings
(CLI flags override saved settings), then dispatches to `run_gui` or `run_cli`.
Return value is the process exit code (`0` success, `1` if any job failed, `2` on
bad arguments / startup error).

**CLI flags (argparse).** All flags are optional; if none of the input flags are given
and no positional URLs are supplied, the GUI launches.

| Flag | Type | Default | Behavior |
|---|---|---|---|
| `--input`, `-i` | path (str) | `None` | Text/CSV file, one URL per line (`#` comments and blank lines ignored). Triggers CLI mode. |
| `--url`, `-u` | str (repeatable) | `[]` | One URL; may be passed multiple times. Triggers CLI mode. |
| `urls` (positional) | str[] | `[]` | Extra URLs as bare positional args. Triggers CLI mode if present. |
| `--output`, `-o` | path (str) | `./downloads` | Output root folder. Created if missing. |
| `--threads`, `-t` | int | `4` | Total worker threads (global concurrency). Range 1–16; clamped. |
| `--per-platform` | int | `2` | Max concurrent jobs per platform. Range 1–8; clamped. |
| `--delay-min` | float | `1.0` | Minimum random pre-download delay, seconds. |
| `--delay-max` | float | `5.0` | Maximum random pre-download delay, seconds. Must be ≥ `--delay-min`. |
| `--retries` | int | `4` | Max retry attempts on retryable errors. Range 0–10. |
| `--cookies` | path (str) | `None` | Cookies file (Netscape format) passed to both engines. |
| `--cookies-from-browser` | str | `None` | Browser name for yt-dlp's `--cookies-from-browser` (e.g. `chrome`, `firefox`, `edge`). Mutually exclusive with `--cookies`. |
| `--quality` | str | `best` | Quality preset: `best`, `1080`, `720`, `480`, `audio`. Maps to yt-dlp format (see §4.3). |
| `--proxy` | str | `None` | Proxy URL (`http://host:port` or `socks5://…`). Stub: stored & passed through, no rotation. |
| `--checkpoint` | path (str) | `<output>/.bmd_checkpoint.json` | Checkpoint file location. |
| `--no-resume` | flag (bool) | `False` | Ignore & do not read the checkpoint (re-download everything). Still writes it. |
| `--log-file` | path (str) | `<output>/bmd.log` | Log file location. |
| `--log-level` | str | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |
| `--cli` | flag (bool) | `False` | Force CLI mode even with no input flags (reads stdin if no URLs). |
| `--gui` | flag (bool) | `False` | Force GUI mode even if input flags are present (input pre-loads the paste box). |
| `--version` | flag | — | Print version and exit. |

**CLI mode flow (`run_cli`).**
1. Collect URLs from `--input` + `--url` + positional; dedupe preserving order.
2. Build `Settings` from merged args.
3. Create `QueueManager`, add jobs, register a console progress printer
   (one line per job update, throttled to ~1/sec per job).
4. `start()` the queue and block until all jobs terminal.
5. Print a summary table: `Total / Done / Failed / Skipped`.
6. Return `0` if no failures, else `1`.

**GUI mode flow (`run_gui`).** Instantiate `QApplication`, `MainWindow(settings)`,
show, `app.exec()`.

---

### 4.2 `core/platform.py` — URL → platform detection

Detects the platform from the URL's host. Matching is **case-insensitive**, done on the
registered domain (strip `www.`, `m.`, `mobile.` subdomain prefixes).

**Domain patterns (host suffix match).**

| Platform | Host matches (any of) |
|---|---|
| `YOUTUBE` | `youtube.com`, `youtu.be`, `youtube-nocookie.com`, `m.youtube.com` |
| `FACEBOOK` | `facebook.com`, `fb.com`, `fb.watch`, `m.facebook.com` |
| `INSTAGRAM` | `instagram.com`, `instagr.am` |
| `TIKTOK` | `tiktok.com`, `vm.tiktok.com`, `vt.tiktok.com` |
| `TWITTER` | `twitter.com`, `x.com`, `t.co`, `mobile.twitter.com` |

**Rules.**
- Parse with `urllib.parse.urlparse`. If no scheme, prepend `https://` before parsing.
- Strip leading `www.`/`m.`/`mobile.` from `netloc`, drop any `:port`, lowercase.
- Match by suffix: host equals a pattern **or** ends with `"." + pattern`.
- No match, empty/invalid URL → `Platform.UNKNOWN`.

**Public interface.**

```python
def detect_platform(url: str) -> Platform: ...
def is_supported(url: str) -> bool:   # True unless UNKNOWN
    ...
def normalize_url(url: str) -> str:   # trim, add https:// if missing, strip tracking? (no)
    ...
```

**Unknown-URL handling.** `detect_platform` returns `UNKNOWN`. The `QueueManager`
still creates the job but marks it `FAILED` immediately with
`error = "Unsupported or unrecognized URL"` and does **not** invoke an engine. (Rationale:
yt-dlp actually supports 1000+ sites, but the graded scope is the 5 platforms; failing
fast keeps behavior predictable. This may be relaxed later behind a setting.)

---

### 4.3 `core/downloader.py` — engine wrapper

Wraps **yt-dlp** and **gallery-dl**. Engine selection is by platform + content type.

**Engine routing.**

| Platform | Default engine | Notes |
|---|---|---|
| YouTube | yt-dlp | Always video/audio. |
| TikTok | yt-dlp | Video. |
| Facebook | yt-dlp | Video/Reels. `fb.watch` etc. |
| X / Twitter | yt-dlp first; if it yields no video, fall back to gallery-dl for image posts | A tweet may be image-only. |
| Instagram | Reels/video → yt-dlp; photo posts/albums → gallery-dl | See selection rule below. |

**Selection rule (`select_engine`).** For the two "mixed" platforms (Instagram, X), the
wrapper tries the **primary** engine and, if it fails with a "no video / unsupported media"
signal (yt-dlp raises `DownloadError` mentioning "There is no video" / "Unsupported URL"),
it retries once with **gallery-dl**. All other platforms use their single mapped engine.
A settings flag `prefer_gallery_dl_for` (list of platforms) can force gallery-dl first.

**yt-dlp integration.** Use the Python API (`yt_dlp.YoutubeDL`), not the subprocess, so
progress hooks work in-process.

- Format string from `quality` preset:

  | `quality` | yt-dlp `format` |
  |---|---|
  | `best` | `bestvideo*+bestaudio/best` |
  | `1080` | `bestvideo[height<=1080]+bestaudio/best[height<=1080]` |
  | `720` | `bestvideo[height<=720]+bestaudio/best[height<=720]` |
  | `480` | `bestvideo[height<=480]+bestaudio/best[height<=480]` |
  | `audio` | `bestaudio/best` (+ `postprocessors` to extract mp3) |

- Options set: `outtmpl` (see filename templates), `noplaylist=False`, `retries=0`
  (retry is handled by our anti_block layer, not yt-dlp's own), `http_headers` with the
  rotated User-Agent, `cookiefile` or `cookiesfrombrowser`, `proxy`, `ratelimit` if set,
  `progress_hooks=[hook]`, `quiet=True`, `no_warnings=True`, `ignoreerrors=False`.
- `ratelimit` (bytes/sec) may be set from settings `rate_limit_bps` (default `None` = off).

**gallery-dl integration.** Prefer the subprocess CLI for robustness (its Python API is
less stable across versions), OR the config-dict + `gallery_dl.job.DownloadJob` API. Spec
target: **subprocess** using `sys.executable -m gallery_dl` with flags:
`-D <dest_dir>`, `--cookies <file>` (or `--cookies-from-browser`), `--proxy <proxy>`,
`--sleep <delay>`, `--range` unlimited, `-o "filename=<template>"`, and a JSON progress
parse via `--write-info-json` or line parsing of `stdout`. Because gallery-dl progress is
coarse, report **indeterminate → 100% on success** (see progress reporting).

**Output filename templates.** Files are organized per platform under the output root.

- Video (yt-dlp `outtmpl`):
  `<output>/<platform>/%(uploader)s/%(title).120B [%(id)s].%(ext)s`
- Images/albums (gallery-dl `filename` / `directory`):
  `<output>/<platform>/{user[name]}/{title|id}_{num}.{extension}`
- Sanitize titles to be Windows-safe (strip `<>:"/\|?*`, collapse whitespace). yt-dlp does
  this via `restrictfilenames=False` + `%(title).120B` byte-truncation; ensure
  `windowsfilenames=True` in yt-dlp options.

**Progress reporting via callback.** The downloader never touches Qt. It reports progress
through a plain callable injected by the caller:

```python
from typing import Callable, Optional

# Progress event pushed to the callback:
@dataclass
class ProgressEvent:
    job_id: int
    status: JobStatus          # RUNNING / DONE / FAILED
    progress: float            # 0..100 (best-effort; may jump 0→100 for gallery-dl)
    title: str = ""
    speed: str = ""
    eta: str = ""
    output_path: str = ""
    error: str = ""

ProgressCallback = Callable[[ProgressEvent], None]
```

**Public interface.**

```python
class Downloader:
    def __init__(self, settings: Settings, anti_block: "AntiBlock") -> None: ...

    def download(
        self,
        job: Job,
        on_progress: ProgressCallback,
        cancel_check: Callable[[], bool] = lambda: False,
    ) -> DownloadResult: ...

    def _download_ytdlp(self, job, on_progress, cancel_check) -> DownloadResult: ...
    def _download_gallery_dl(self, job, on_progress, cancel_check) -> DownloadResult: ...
    def select_engine(self, platform: Platform) -> str:   # "ytdlp" | "gallery_dl"
        ...

@dataclass
class DownloadResult:
    ok: bool
    output_path: str = ""
    title: str = ""
    error: str = ""
    retryable: bool = False    # True if the error is a rate-limit/transient error
```

- `cancel_check()` is polled inside progress hooks; if it returns `True`, raise a
  `CancelledError` that surfaces as `JobStatus.CANCELLED` (not a failure).
- **Error surfacing.** Engine exceptions are caught in `download()` and classified by
  `AntiBlock.classify_error(exc)` into retryable vs fatal. `download()` itself does **one**
  attempt; the retry loop lives in `AntiBlock.run_with_retry` (see §4.4) which wraps
  `download()`. `DownloadResult.retryable` communicates the classification.

---

### 4.4 `core/anti_block.py` — anti-blocking layer (core)

Centralizes every anti-blocking technique with concrete defaults.

**Techniques & defaults.**

| Technique | Default | Detail |
|---|---|---|
| Random pre-download delay | `1.0–5.0 s` (`delay_min`/`delay_max`) | `time.sleep(random.uniform(min,max))` before each attempt. |
| Exponential backoff retry | base `2 s`, factor `2`, cap `60 s`, jitter ±25% | Wait = `min(cap, base * 2**(attempt-1))` then `* uniform(0.75,1.25)`. |
| Max retries | `4` (`retries`) | Total attempts = `retries + 1`. |
| Retryable HTTP codes | `429, 403, 408, 500, 502, 503, 504` | Plus transient network errors (timeouts, conn reset). |
| Non-retryable | `404, 401 (unless cookies unset)`, "Unsupported URL", "Private/removed" | Fail fast. |
| User-Agent rotation | pool of ~10 modern desktop UAs (see below) | New UA chosen per attempt via `next_user_agent()`. |
| Per-platform concurrency cap | `2` (`per_platform`) | Enforced by `QueueManager` via per-platform semaphores (§4.5). |
| Cookies | off by default | `cookies_file` (Netscape) or `cookies_from_browser`. Passed to both engines. |
| Rate limit (bytes/s) | `None` (off) | Optional yt-dlp `ratelimit` / gallery-dl `--limit-rate`. |
| Proxy (stub) | `None` | Stored, passed to engines as-is; **no rotation yet**. |

**User-Agent list source.** Ship a static list `USER_AGENTS: list[str]` embedded in
`anti_block.py` (~10 current Chrome/Firefox/Edge desktop strings for Win10/11 and macOS).
No network dependency. A comment documents the "last updated" date so it can be refreshed.
`next_user_agent()` returns `random.choice(USER_AGENTS)`.

**Backoff schedule (with defaults, before jitter).**

| Attempt | Wait before retry |
|---|---|
| 1 → 2 | 2 s |
| 2 → 3 | 4 s |
| 3 → 4 | 8 s |
| 4 → 5 | 16 s |
| (cap) | 60 s |

**Public interface.**

```python
class AntiBlock:
    def __init__(self, settings: Settings) -> None: ...

    def pre_request_delay(self) -> None:
        """Sleep a random uniform delay in [delay_min, delay_max]."""

    def next_user_agent(self) -> str: ...

    def backoff_wait(self, attempt: int) -> float:
        """Return (and sleep) the backoff duration for the given 1-based attempt."""

    def classify_error(self, exc: BaseException) -> "ErrorClass":
        """Map an engine/network exception to RETRYABLE / FATAL / AUTH."""

    def http_headers(self) -> dict[str, str]:
        """Headers dict including a fresh rotated User-Agent."""

    def cookies_args(self) -> dict:
        """Return {'cookiefile': ...} or {'cookiesfrombrowser': (...)} or {}."""

    def proxy(self) -> Optional[str]:
        """Return the configured proxy URL (stub — single value, no rotation)."""

    def run_with_retry(
        self,
        attempt_fn: Callable[[int], DownloadResult],
        job: Job,
        cancel_check: Callable[[], bool] = lambda: False,
    ) -> DownloadResult:
        """
        Loop up to settings.retries+1 times:
          - pre_request_delay()
          - result = attempt_fn(attempt_number)
          - if ok or not retryable -> return
          - else backoff_wait(attempt) and retry
        Honors cancel_check between attempts.
        """

class ErrorClass(str, Enum):
    RETRYABLE = "retryable"
    FATAL     = "fatal"
    AUTH      = "auth"     # login/cookies required -> fatal but distinct message
```

**Proxy stub interface (later extension).** `proxy()` returns a single URL from
`settings.proxy` or `None`. The interface is intentionally shaped for future rotation:
a later `ProxyPool` class can implement `def proxy() -> Optional[str]` returning a
different IP per call. No pool, health-check, or rotation is implemented now.

---

### 4.5 `core/queue_manager.py` — queue + worker pool

Owns the job list, the worker pool, and per-platform concurrency enforcement. It emits
events to the UI/CLI without importing UI code.

**Concurrency model.** Use **`concurrent.futures.ThreadPoolExecutor`** (I/O-bound work;
simpler than QThreadPool for the core and reusable by the CLI). Global concurrency =
`settings.threads`. Per-platform cap enforced with a `threading.Semaphore` per platform
initialized to `settings.per_platform`; a worker acquires the platform semaphore around
the actual download, so even with N global threads no more than `per_platform` run for one
platform at once.

**Event delivery.** `QueueManager` accepts listener callbacks; it does not know about Qt.
`ui/main_window.py` wraps callbacks that re-emit as Qt signals (marshalled to the GUI
thread — see §4.7). CLI wraps callbacks that print. All listener calls happen on worker
threads; listeners must be thread-safe or marshal.

```python
JobEventCallback = Callable[[Job], None]   # called on any job state/progress change

class QueueManager:
    def __init__(
        self,
        settings: Settings,
        downloader: Downloader,
        anti_block: AntiBlock,
        checkpoint: CheckpointStore,
    ) -> None: ...

    def add_url(self, url: str) -> Job: ...
    def add_urls(self, urls: Iterable[str]) -> list[Job]: ...
    def load_from_file(self, path: str) -> list[Job]: ...

    def on_job_event(self, cb: JobEventCallback) -> None:   # register listener
        ...

    def start(self) -> None:
        """Submit all QUEUED jobs to the pool. Non-blocking."""
    def wait(self, timeout: float | None = None) -> bool:
        """Block until all jobs terminal (for CLI). Returns True if finished."""
    def stop(self) -> None:
        """Signal cancellation: no new jobs start; running jobs finish or abort;
        remaining QUEUED jobs -> CANCELLED."""

    @property
    def jobs(self) -> list[Job]: ...
    def summary(self) -> "QueueSummary": ...

@dataclass
class QueueSummary:
    total: int
    done: int
    failed: int
    skipped: int
    running: int
    remaining: int
```

**Per-job worker routine (runs in pool thread).**
1. If `stop` requested → set `CANCELLED`, emit, return.
2. If `platform == UNKNOWN` → `FAILED` ("Unsupported or unrecognized URL"), emit, return.
3. If checkpoint has `url_hash` and not `no_resume` → `SKIPPED`, emit, return.
4. Set `RUNNING`, emit.
5. Acquire the platform semaphore.
6. Call `anti_block.run_with_retry(attempt_fn=lambda n: downloader.download(job, on_progress, cancel_check), job, cancel_check)`.
7. On success → `DONE`, write checkpoint, emit. On failure → `FAILED` with error, emit.
   On cancel → `CANCELLED`, emit.
8. Release the platform semaphore (in `finally`).

**Thread-safety notes.**
- `Job` objects are mutated only by their owning worker thread; the UI reads snapshots.
  When emitting, pass either the `Job` (UI must only read) or a shallow copy.
- Shared counters (`summary`) computed on demand under a `threading.Lock`.
- The listener list is set up before `start()`; treat it as read-only afterward.
- `stop()` sets a `threading.Event`; workers poll it via `cancel_check`.

---

### 4.6 `core/checkpoint.py` — state persistence & resume

**File format.** JSON, single object. Location default:
`<output>/.bmd_checkpoint.json` (overridable via `--checkpoint` / setting).

```json
{
  "version": 1,
  "entries": {
    "<sha256(url)>": {
      "url": "https://…",
      "platform": "youtube",
      "output_path": "D:\\media\\youtube\\…\\file.mp4",
      "title": "Video ABC",
      "finished_at": 1720000000.0
    }
  }
}
```

**Done key.** `sha256(url)` hex digest (matches `Job.url_hash`). URL is used verbatim
(after `normalize_url`) so the same link hashes identically across runs.

**Public interface.**

```python
class CheckpointStore:
    def __init__(self, path: str) -> None: ...
    def load(self) -> None:
        """Read the JSON file into memory. Missing/corrupt file → empty, log a warning."""
    def is_done(self, url_hash: str) -> bool: ...
    def mark_done(self, job: Job) -> None:
        """Add an entry and persist (atomic write: temp file + os.replace)."""
    def remove(self, url_hash: str) -> None: ...
    def clear(self) -> None: ...
    def all(self) -> dict[str, dict]: ...
```

- **Atomic writes.** Write to `path + ".tmp"` then `os.replace` to avoid corruption on
  crash. Guard concurrent `mark_done` with a `threading.Lock`.
- **Resume behavior.** With `--no-resume` / `resume=False`, `is_done` always returns
  `False` (skip logic bypassed) but `mark_done` still writes, so a fresh run rebuilds it.

---

### 4.7 `ui/main_window.py` — main window

**Widgets & layout (top to bottom).**

1. **Header row:** app title label (left) + `⚙ Settings` button (right, opens
   `SettingsDialog`).
2. **URL input area:** a `QPlainTextEdit` (multi-line paste, one URL/line) + `Import
   from file…` button (`QFileDialog`, `.txt`/`.csv`).
3. **Config row:** `Save to:` `QLineEdit` + `Browse…` button; `Threads:` `QSpinBox`
   (1–16, default 4); `Cookies:` `Choose…` button/label; `Proxy` checkbox (disabled,
   tooltip "later"). These mirror a subset of settings for convenience.
4. **Action row:** `▶ START DOWNLOAD` button (toggles to `■ STOP` while running).
5. **Jobs table:** `QTableView` + a custom `QAbstractTableModel` (`JobsTableModel`).
6. **Status bar:** summary label `Total: N | Done: N | Failed: N | Left: N` +
   `Export log…` button.

**Jobs table columns.**

| # | Column | Source | Notes |
|---|---|---|---|
| 0 | `#` | `Job.id` | right-aligned |
| 1 | `Platform` | `Job.platform` | title-cased |
| 2 | `Title` | `Job.title` | elided |
| 3 | `Status` | `Job.status` | colored text + icon (⏳/⬇/✅/❌) |
| 4 | `Progress` | `Job.progress` | `QStyledItemDelegate` drawing a progress bar |
| 5 | `Speed/ETA` | `Job.speed`,`Job.eta` | optional, small |

**Start/stop the queue.**
- On `START`: read URLs from the text box, build a `Settings` from GUI + persisted config,
  construct `QueueManager`+`Downloader`+`AntiBlock`+`CheckpointStore`, populate
  `JobsTableModel`, register the event bridge (below), call `queue.start()`, flip the
  button to `STOP`.
- On `STOP`: call `queue.stop()`, disable the button until workers drain, then reset.

**Progress signals (Qt signals/slots).** `QueueManager` runs listeners on worker threads;
Qt widgets must be touched only on the GUI thread. Bridge via a `QObject` with a signal:

```python
class QueueBridge(QObject):
    job_updated = Signal(object)     # emits a Job snapshot
    summary_updated = Signal(object) # emits QueueSummary

    def on_job_event(self, job: Job) -> None:
        # called on worker thread -> emit; Qt queues it to the GUI thread
        self.job_updated.emit(copy_job(job))
```

- `MainWindow` connects `bridge.job_updated` → `JobsTableModel.update_job` slot (runs on
  GUI thread) and `bridge.summary_updated` → `update_status_bar`.
- `QueueManager.on_job_event(bridge.on_job_event)` wires worker→bridge.
- Signal/slot connections use the default `AutoConnection`, which marshals cross-thread
  emissions onto the GUI thread — this is the thread-safety mechanism.

**Export log.** `Export log…` writes a CSV (`#,platform,url,status,title,error,output_path`)
of all jobs to a user-chosen path.

---

### 4.8 `ui/settings_dialog.py` — configuration dialog

Modal `QDialog` editing the persisted `Settings`. `OK` saves via `core/config.py`;
`Cancel` discards.

**Fields.**

| Field | Widget | Setting key | Default |
|---|---|---|---|
| Output folder | `QLineEdit` + Browse | `output_dir` | `./downloads` |
| Thread count | `QSpinBox` (1–16) | `threads` | `4` |
| Per-platform cap | `QSpinBox` (1–8) | `per_platform` | `2` |
| Delay min (s) | `QDoubleSpinBox` (0–60) | `delay_min` | `1.0` |
| Delay max (s) | `QDoubleSpinBox` (0–60) | `delay_max` | `5.0` |
| Max retries | `QSpinBox` (0–10) | `retries` | `4` |
| Cookies file | `QLineEdit` + Browse | `cookies_file` | `""` |
| Cookies from browser | `QComboBox` (none/chrome/firefox/edge) | `cookies_from_browser` | `""` |
| Quality | `QComboBox` (best/1080/720/480/audio) | `quality` | `best` |
| Rate limit (KB/s, 0=off) | `QSpinBox` | `rate_limit_kbps` | `0` |
| Proxy enabled | `QCheckBox` (disabled) | `proxy_enabled` | `False` |
| Proxy address | `QLineEdit` (disabled) | `proxy` | `""` |
| Log level | `QComboBox` | `log_level` | `INFO` |

**Validation.** Enforce `delay_max ≥ delay_min` (swap or warn). Proxy fields disabled with
tooltip "Proxy rotation is a later extension."

**Persistence.** Settings persist via **`QSettings`** (organization `"BMD"`, application
`"BulkMediaDownloader"`) → Windows registry / INI. `core/config.py` provides load/save
that maps `QSettings` ↔ the `Settings` dataclass so the CLI (which may not want registry
writes) can also construct `Settings` purely from argparse.

---

## 5. Config / settings (single source of truth)

`core/config.py` defines:

```python
@dataclass
class Settings:
    output_dir: str = "./downloads"
    threads: int = 4
    per_platform: int = 2
    delay_min: float = 1.0
    delay_max: float = 5.0
    retries: int = 4
    cookies_file: str = ""
    cookies_from_browser: str = ""
    quality: str = "best"                 # best|1080|720|480|audio
    rate_limit_kbps: int = 0              # 0 = unlimited
    proxy_enabled: bool = False
    proxy: str = ""
    resume: bool = True                   # False == --no-resume
    checkpoint_path: str = ""             # "" => <output_dir>/.bmd_checkpoint.json
    log_file: str = ""                    # "" => <output_dir>/bmd.log
    log_level: str = "INFO"

    def load_from_qsettings(self) -> "Settings": ...
    def save_to_qsettings(self) -> None: ...
    @classmethod
    def from_args(cls, args, base: "Settings | None" = None) -> "Settings": ...
```

**Full settings table.**

| Key | Type | Default | Range/values |
|---|---|---|---|
| `output_dir` | str | `./downloads` | any writable path |
| `threads` | int | `4` | 1–16 |
| `per_platform` | int | `2` | 1–8 |
| `delay_min` | float | `1.0` | 0–60 |
| `delay_max` | float | `5.0` | ≥ delay_min, ≤ 60 |
| `retries` | int | `4` | 0–10 |
| `cookies_file` | str | `""` | Netscape cookies path |
| `cookies_from_browser` | str | `""` | `""`/chrome/firefox/edge |
| `quality` | str | `best` | best/1080/720/480/audio |
| `rate_limit_kbps` | int | `0` | 0 = off |
| `proxy_enabled` | bool | `False` | (stub) |
| `proxy` | str | `""` | url (stub) |
| `resume` | bool | `True` | — |
| `checkpoint_path` | str | `""` (→ derived) | path |
| `log_file` | str | `""` (→ derived) | path |
| `log_level` | str | `INFO` | DEBUG/INFO/WARNING/ERROR |

**Backoff constants** (in `anti_block.py`, not user-facing): `BACKOFF_BASE = 2.0`,
`BACKOFF_FACTOR = 2.0`, `BACKOFF_CAP = 60.0`, `BACKOFF_JITTER = 0.25`,
`RETRYABLE_HTTP = {429, 403, 408, 500, 502, 503, 504}`.

---

## 6. `requirements.txt`

```
PySide6>=6.6,<7
yt-dlp>=2024.1.0
gallery-dl>=1.26
requests>=2.31
```

Notes:
- `requests` used only for header/UA helpers and simple checks (optional; can be dropped if
  unused).
- yt-dlp benefits from **ffmpeg** on PATH for merging video+audio and mp3 extraction.
  Not a pip package — document in README; `build.bat`/README instruct bundling `ffmpeg.exe`
  next to the `.exe` or installing it. If ffmpeg is missing, fall back to a progressive
  single-file format (`best[ext=mp4]/best`) and log a warning.
- Dev/build extras (not runtime): `pyinstaller>=6.3` (keep in a `requirements-dev.txt` or a
  comment).

---

## 7. Error handling & logging

**Logging.** Use the stdlib `logging` module, configured once in `app.py` (both modes).

- **Handlers:** a `RotatingFileHandler` (`log_file`, `maxBytes=5_000_000`, `backupCount=3`)
  and a `StreamHandler` (stderr; in CLI mode also stdout for progress lines).
- **Format:** `%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s`
- **Loggers:** module-level `logger = logging.getLogger(__name__)` per module.
- **Level:** from `settings.log_level` (default `INFO`). yt-dlp/gallery-dl internal
  chatter suppressed (`quiet=True`); their errors are captured and re-logged by us.

**Per-job failure recording.**
- On `FAILED`, set `Job.error` to a concise reason (first line of the engine error, or the
  `ErrorClass` message for auth/unsupported).
- Log at `ERROR` with job id, url, platform, attempts, and the error.
- The exported result CSV includes the `error` column so failures are auditable.
- Retries logged at `WARNING`: `"job %d attempt %d/%d failed (%s), backing off %.1fs"`.

**Exception policy.**
- Engine calls wrapped in `try/except`; never let a worker exception kill the pool.
- `CancelledError` → `CANCELLED` (not logged as error).
- Unexpected exceptions → `FAILED`, logged at `ERROR` with `exc_info=True`.

---

## 8. Acceptance criteria / test checklist

The later test phase must be able to execute these. `<OUT>` is a temp output folder.

**Platform detection (unit).**
- [ ] AC1. `detect_platform("https://www.youtube.com/watch?v=x")` → `YOUTUBE`.
- [ ] AC2. `youtu.be`, `fb.watch`, `vm.tiktok.com`, `x.com`, `instagr.am` map to the right
      platforms; `https://example.com/x` → `UNKNOWN`.
- [ ] AC3. URL without scheme (`youtube.com/watch?v=x`) still detects `YOUTUBE`.

**CLI mode.**
- [ ] AC4. `links.txt` with one YouTube URL: `python app.py --input links.txt --output <OUT>
      --threads 2` downloads the file into `<OUT>/youtube/…` and exits `0`.
- [ ] AC5. After AC4, `<OUT>/.bmd_checkpoint.json` contains one entry keyed by
      `sha256(url)` with the correct `platform` and `output_path`.
- [ ] AC6. Re-running the same command marks the job `SKIPPED` (no re-download); with
      `--no-resume` it downloads again.
- [ ] AC7. An `UNKNOWN` URL in the list is reported `FAILED` with
      "Unsupported or unrecognized URL", other jobs still complete; exit code `1`.
- [ ] AC8. `--threads` and `--per-platform` are respected: with 5 YouTube URLs,
      `--per-platform 2` never runs more than 2 YouTube downloads concurrently (verify via
      logs/timing).

**Anti-block.**
- [ ] AC9. A random delay in `[delay_min, delay_max]` occurs before each attempt (assert via
      injected clock / timing).
- [ ] AC10. Given a stubbed engine that raises a 429 twice then succeeds, `run_with_retry`
      retries with increasing backoff and finally returns `ok=True` within `retries+1`
      attempts.
- [ ] AC11. A `404`/"Unsupported URL" error is classified `FATAL` and is **not** retried.
- [ ] AC12. `next_user_agent()` returns a value from `USER_AGENTS`; headers include it.

**Downloader routing.**
- [ ] AC13. `select_engine(INSTAGRAM)` returns yt-dlp first; a photo-only post falls back to
      gallery-dl (mock the yt-dlp "no video" error).
- [ ] AC14. Output filename follows the template and is Windows-safe (no `:*?` etc.).

**Checkpoint.**
- [ ] AC15. `CheckpointStore` survives a corrupt JSON file (loads empty, logs a warning).
- [ ] AC16. `mark_done` writes atomically (a killed process mid-write leaves the previous
      valid file intact — simulate via temp-file assertion).

**GUI (smoke).**
- [ ] AC17. Launching with no args opens the main window; pasting a URL and clicking START
      adds a row and the Status/Progress columns update to `Done` on success.
- [ ] AC18. `Export log…` writes a CSV with one row per job including the `error` column.
- [ ] AC19. Settings dialog persists values (reopen shows saved values); proxy fields are
      disabled.

**Cross-cutting.**
- [ ] AC20. A run of ≥20 mixed-platform URLs completes with correct
      `Total/Done/Failed/Skipped` counters and a populated `bmd.log`.

---

## 9. Non-goals / out of scope

- **Upload / posting** to any platform — download only.
- **Custom crawlers / reverse-engineered private APIs** — all fetching goes through yt-dlp
  and gallery-dl.
- **Full proxy rotation** — only a single passthrough proxy stub now; a `ProxyPool` with
  rotation/health-checks is a later extension (interface is pre-shaped in `anti_block.py`).
- **CAPTCHA solving, account creation, or auth bypass** — cookies are user-supplied for
  content they can access; no security-mechanism bypass.
- **Non-Windows packaging** — macOS/Linux may run from source for dev, but only a Windows
  `.exe` is a shipping deliverable.
- **Post-processing beyond basic ffmpeg merge/mp3** — no transcoding presets, no editing.
- **Scheduling / daemon mode / browser extension** — out of scope for this timeline.

---

## 10. Notes for implementers (2-day plan alignment)

- **Day 1 (core, CLI-testable):** `config.py`, `platform.py`, `anti_block.py`,
  `checkpoint.py`, `downloader.py`, `queue_manager.py`, `app.py` CLI path. Validate with
  AC1–AC16.
- **Day 2 (GUI + polish):** `main_window.py`, `settings_dialog.py`, event bridge, resume in
  UI, log export, PyInstaller `build.bat`, README. Validate AC17–AC20 + real 5-platform run.
- Keep the core **Qt-free** so the CLI and unit tests need no `QApplication`. Only `ui/*`
  and `run_gui` import PySide6.
```
