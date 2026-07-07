# Bulk Media Downloader

Download large volumes of media (videos and image albums) from **YouTube,
Facebook, Instagram, TikTok, and X (Twitter)** without being rate-limited or
blocked. It wraps two mature engines — **yt-dlp** (video/audio) and
**gallery-dl** (images/albums) — inside a custom orchestration layer that adds a
job queue, a controlled multithreaded worker pool, an **anti-blocking layer**,
checkpoint/resume, progress reporting, and both a **GUI** and a **CLI**.

> Responsible use: this tool targets content you are entitled to download. The
> anti-blocking techniques keep large batches stable; they are not intended to
> bypass authentication or platform security. You are responsible for complying
> with each platform's terms of service and applicable law.

📖 **New user? Start with the step-by-step [User Guide](docs/GUIDE.md).** This
README is the quick reference; `docs/SOLUTION.md` and `docs/SPEC.md` cover the
design and technical spec.

---

## Features

- Bulk input: paste many URLs or import a `.txt` / `.csv` list.
- Automatic platform detection and engine routing.
- Controlled multithreaded downloading with **per-platform** concurrency caps.
- **Anti-blocking layer:** random delay, exponential-backoff retry, User-Agent
  rotation, login cookies, optional rate limit, proxy pool (rotation, health-check, cooldown).
- **Checkpoint/resume:** finished URLs are skipped on the next run.
- Runs identically from a **GUI** and the **command line**.
- Per-job status table and CSV result export.

---

## Requirements

- **Python 3.11+**
- Dependencies in `requirements.txt`:
  - `PySide6` (GUI only), `yt-dlp`, `gallery-dl`, `requests`
- **ffmpeg** (recommended, not a pip package): needed to merge separate
  video+audio streams and to extract mp3. If ffmpeg is missing, the app falls
  back to a single progressive format and logs a warning. Put `ffmpeg.exe` on
  your `PATH` (or next to the built `.exe`).

### Install

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

---

## Usage — GUI

```bash
python app.py
```

Paste URLs (one per line) or **Import from file…**, choose an output folder and
thread count, then **START DOWNLOAD**. The jobs table shows per-row Platform,
Title, Status, and a live Progress bar. **⚙ Settings** exposes every option;
**Export log…** writes a CSV of all jobs.

---

## Usage — CLI

CLI mode activates automatically when any input flag (`--input`, `--url`,
positional URLs, or `--cli`) is present. Example:

```bash
python app.py --input links.txt --output ./downloads --threads 4
python app.py -u "https://youtube.com/watch?v=..." -u "https://x.com/..." -o ./out
```

### Key flags

| Flag | Default | Purpose |
|---|---|---|
| `--input`, `-i` | – | URL list file (`#` comments ignored). |
| `--url`, `-u` | – | A single URL (repeatable). |
| `--output`, `-o` | `./downloads` | Output root folder. |
| `--threads`, `-t` | `4` | Total worker threads (1–16). |
| `--per-platform` | `2` | Max concurrent jobs per platform (1–8). |
| `--delay-min` / `--delay-max` | `1.0` / `5.0` | Random pre-download delay range (s). |
| `--retries` | `4` | Retry attempts on transient errors (0–10). |
| `--cookies` | – | Netscape cookies file (both engines). |
| `--cookies-from-browser` | – | `chrome`/`firefox`/`edge` (yt-dlp). |
| `--quality` | `best` | `best`/`1080`/`720`/`480`/`audio`. |
| `--proxy` | – | A single proxy URL, e.g. `http://ip:port`. |
| `--proxy-rotate` | off | Rotate through a pool of free public proxies. |
| `--proxy-source` | built-in | Custom proxy-list URL (repeatable); overrides the built-in free lists. |
| `--no-proxy-validate` | validate on | Skip health-checking proxies before use. |
| `--checkpoint` | `<output>/.bmd_checkpoint.json` | Checkpoint file. |
| `--no-resume` | off | Ignore the checkpoint (re-download all). |
| `--log-file` | `<output>/bmd.log` | Log file. |
| `--log-level` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |
| `--cli` / `--gui` | – | Force CLI / GUI mode. |

Exit codes: `0` success, `1` if any job failed, `2` on bad arguments/startup.

See all options:

```bash
python app.py --help
```

A ready-made `sample_links.txt` is included (one example URL per platform).

---

## How it avoids blocks (anti-blocking layer)

| Technique | Default |
|---|---|
| Random pre-download delay | 1–5 s |
| Exponential backoff retry | 2s → 4s → 8s … cap 60s, ±25% jitter |
| Max retries | 4 (5 attempts total) |
| User-Agent rotation | ~10 modern desktop UAs, rotated per attempt |
| Per-platform concurrency cap | 2 |
| Login cookies | file or from-browser (optional) |
| Rate limit | off by default |
| Proxy rotation | free-proxy pool with round-robin, health-check & cooldown (opt-in) |

Retryable errors: HTTP `429, 403, 408, 500, 502, 503, 504`, transient network
errors, and connection/proxy-level failures (SSL/EOF, tunnel-connect, proxy
errors) — common with free proxies, so retrying rotates to a fresh IP. When
proxy rotation is on, an IP bot-challenge ("confirm you're not a bot") is also
retried so it cycles to a different IP. Fatal errors (`404`, "Unsupported URL",
private/removed, genuine login-required) fail fast.

Checkpoint/resume: completed URLs are keyed by `sha256(url)` in
`<output>/.bmd_checkpoint.json` and skipped next run (unless `--no-resume`).

### Proxy rotation

Enable IP rotation to spread a large batch across many IPs and dodge per-IP rate
limits. The pool is fetched from built-in free lists (ProxyScrape, Proxifly,
TheSpeedX, ProxyScraper), health-checked, then rotated round-robin; a proxy that
fails a download is cooled down and dropped after repeated failures. When no
proxy is usable, downloads fall back to a direct connection.

```bash
# Rotate through free public proxies (health-checked first):
python app.py --input links.txt --proxy-rotate

# Faster start, skip validation (less reliable):
python app.py --input links.txt --proxy-rotate --no-proxy-validate

# Use your own (e.g. paid residential) list instead of the free sources:
python app.py --input links.txt --proxy-rotate --proxy-source https://example.com/my-proxies.txt
```

> ⚠️ **Free proxies are unreliable, slow, and a security risk** — many are dead,
> some log or tamper with traffic. Never send credentials through them. For
> serious large-volume work, supply a paid residential/mobile list via
> `--proxy-source`.

---

## Build a Windows `.exe` (PyInstaller)

```bash
pip install -r requirements-dev.txt
build.bat
```

This produces `dist\BulkMediaDownloader.exe`. Ship `ffmpeg.exe` alongside it for
video+audio merging and mp3 extraction. `build.bat` uses `--collect-all` for
`yt_dlp` and `gallery_dl` so their data files are bundled.

---

## Project layout

```
bulk-media-downloader/
├── app.py                 # Entry point: GUI (default) + CLI (argparse)
├── core/                  # Qt-free core (usable headless / in tests)
│   ├── platform.py        # URL → Platform detection
│   ├── downloader.py      # yt-dlp / gallery-dl wrapper + progress
│   ├── anti_block.py      # delay, backoff, UA rotation, cookies, proxy rotation
│   ├── proxy_pool.py      # free-proxy fetch, health-check, rotation, cooldown
│   ├── queue_manager.py   # Job model, worker pool, per-platform caps
│   ├── checkpoint.py      # JSON state file, sha256(url) done-key
│   └── config.py          # Settings dataclass + load/save
├── ui/                    # PySide6 GUI (imported only in GUI mode)
│   ├── main_window.py
│   └── settings_dialog.py
├── requirements.txt
├── requirements-dev.txt
├── build.bat
└── sample_links.txt
```

The `core` package never imports Qt, so the CLI and unit tests run without a
`QApplication` (and without PySide6 installed at all).
