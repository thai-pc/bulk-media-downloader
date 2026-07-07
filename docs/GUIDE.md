# User Guide — Bulk Media Downloader

A step-by-step guide to installing and running the app, using the **GUI** first,
then the command line, proxies, cookies, and troubleshooting.

> New here? Read this top to bottom once — it takes you from an empty machine to
> a finished bulk download. For a terse reference of every flag, see `README.md`.
> For the technical design, see `SOLUTION.md` and `SPEC.md`.

---

## 1. What this app does

It downloads large numbers of videos and image albums from **YouTube, Facebook,
Instagram, TikTok, and X (Twitter)** in one batch, while avoiding platform rate
limits/blocks (random delays, retry with backoff, User-Agent rotation,
per-platform concurrency caps, optional proxy rotation, checkpoint/resume). It
runs as a **desktop GUI** or from **PowerShell/CMD/Terminal**.

---

## 2. Install (one time)

You need **Python 3.11 or newer**. Check with:

```bash
python --version        # Windows
python3 --version       # macOS/Linux
```

Then, from the project folder `bulk-media-downloader/`:

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

This installs PySide6 (the GUI), yt-dlp, gallery-dl, and requests into an
isolated `.venv` so nothing pollutes your system Python.

### Recommended: install ffmpeg

`ffmpeg` is **not** a pip package but is strongly recommended — it merges
separate video+audio streams (needed for the best YouTube quality) and extracts
MP3 for the `audio` preset. Without it, the app still works but falls back to a
single lower-quality file and logs a warning.

- **Windows:** download `ffmpeg.exe` and put it on your `PATH` (or next to the
  built `.exe`).
- **macOS:** `brew install ffmpeg`
- **Linux:** `sudo apt install ffmpeg` (or your distro's package).

---

## 3. Run with the UI (recommended)

With the virtualenv active, launch the app **with no arguments** — that opens the
graphical interface:

```bash
# Windows
python app.py

# macOS/Linux
python3 app.py
```

> Tip: if you didn't activate the venv, call its Python directly:
> `\.venv\Scripts\python app.py` (Windows) or `.venv/bin/python app.py` (macOS/Linux).

### The main window, step by step

```
┌────────────────────────────────────────────────────────────┐
│  Bulk Media Downloader                          [⚙ Settings]│
├────────────────────────────────────────────────────────────┤
│  Paste URLs (one per line):        [📂 Import from file...] │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ https://youtube.com/watch?v=...                      │  │
│  │ https://www.tiktok.com/@user/video/...               │  │
│  └──────────────────────────────────────────────────────┘  │
│  Save to: [ ...output folder... ] [Browse…]  Threads: [4]  │
│  [Cookies: Choose…]   [☑ Proxy rotation]                   │
│                                   [ ▶ START DOWNLOAD ]     │
├────────────────────────────────────────────────────────────┤
│  #  Platform  Title            Status       Progress  Spd  │
│  1  YouTube   Video ABC        Downloading  ███░ 62%  1.2M │
│  2  TikTok    Clip XYZ         ✅ Done       100%          │
│  3  Instagram Album (10 img)   ⏳ Waiting    –             │
├────────────────────────────────────────────────────────────┤
│  Total: 150 | Done: 87 | Failed: 2 | Left: 61 [Export log…]│
└────────────────────────────────────────────────────────────┘
```

1. **Add URLs** — paste them into the big box (one link per line), or click
   **Import from file…** to load a `.txt`/`.csv` list. Lines starting with `#`
   are treated as comments.
2. **Choose where to save** — click **Browse…** and pick an output folder.
3. **Set threads** — how many downloads run at once (default `4`). Higher = faster
   but more likely to be rate-limited; the per-platform cap (default `2`) still
   protects each site.
4. *(Optional)* **Cookies** — click **Cookies: Choose…** to load a cookies file
   for content that requires login (see §6).
5. *(Optional)* **Proxy rotation** — tick it to route downloads through rotating
   proxies (see §5).
6. Click **▶ START DOWNLOAD**. The button becomes **■ STOP** while running.
7. Watch the **jobs table**: each row shows Platform, Title, Status
   (Waiting → Downloading → Done/Failed/Skipped), a live progress bar, and
   speed/ETA. The bottom bar tallies **Total / Done / Failed / Left**.
8. When finished, click **Export log…** to save a CSV report of every job.

### The Settings dialog (⚙)

Click **⚙ Settings** for every option. It persists between runs.

| Setting | Default | Notes |
|---|---|---|
| Output folder | `./downloads` | Where files are saved. |
| Thread count | `4` | Total concurrent downloads (1–16). |
| Per-platform cap | `2` | Max concurrent jobs per site (1–8). |
| Delay min / max (s) | `1.0` / `5.0` | Random pause before each request. |
| Max retries | `4` | Retry attempts on transient errors (0–10). |
| Cookies file | – | Netscape-format cookies (both engines). |
| Cookies from browser | – | `chrome`/`firefox`/`edge` (yt-dlp reads them). |
| Quality | `best` | `best`/`1080`/`720`/`480`/`audio`. |
| Rate limit | `0` (off) | KB/s cap per download. |
| Proxy enabled | off | Turn on the proxy layer. |
| Proxy address | – | A single `http://ip:port` (used when rotation is off). |
| Rotate free proxies | off | Fetch + rotate a pool of free proxies. |
| Health-check proxies | on | Test proxies before use (recommended). |
| Proxy sources | – | Your own proxy-list URLs (one per line); blank = built-in free lists. |
| Log level | `INFO` | `DEBUG` for verbose troubleshooting. |

---

## 4. Run from the command line

CLI mode activates automatically whenever you pass an input flag (`--input`,
`--url`, positional URLs, or `--cli`). Same engine as the GUI, no window.

```bash
# A list file, 4 threads, 480p:
python app.py --input links.txt --output ./downloads --threads 4 --quality 480

# A couple of URLs inline:
python app.py -u "https://youtube.com/watch?v=..." -u "https://x.com/i/status/..." -o ./out

# Audio-only (needs ffmpeg):
python app.py --input links.txt --quality audio
```

Most-used flags (full list: `python app.py --help`):

| Flag | Default | Purpose |
|---|---|---|
| `--input`, `-i` | – | URL list file (`#` comments ignored). |
| `--url`, `-u` | – | A single URL (repeatable). |
| `--output`, `-o` | `./downloads` | Output root folder. |
| `--threads`, `-t` | `4` | Total worker threads (1–16). |
| `--per-platform` | `2` | Max concurrent jobs per platform (1–8). |
| `--quality` | `best` | `best`/`1080`/`720`/`480`/`audio`. |
| `--cookies` | – | Netscape cookies file. |
| `--proxy-rotate` | off | Rotate through free proxies. |
| `--no-resume` | off | Ignore the checkpoint (re-download all). |
| `--log-level` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |

Exit codes: `0` success · `1` at least one job failed · `2` bad arguments/startup.

---

## 5. Proxy rotation (for large batches)

Downloading many items from one IP invites rate limits and IP blocks. Proxy
rotation spreads requests across many IPs.

**GUI:** tick **Proxy rotation**, then open **⚙ Settings** to keep
**Health-check proxies** on (recommended) and, optionally, paste your own
**Proxy sources**.

**CLI:**
```bash
# Use built-in free lists (fetched + health-checked, then rotated):
python app.py --input links.txt --proxy-rotate

# Skip validation for a faster start (less reliable):
python app.py --input links.txt --proxy-rotate --no-proxy-validate

# Use your own (e.g. paid residential) list instead of the free sources:
python app.py --input links.txt --proxy-rotate --proxy-source https://example.com/my-proxies.txt
```

How it behaves: the pool is fetched from free lists, health-checked, then rotated
round-robin. A proxy that fails is cooled down and dropped after repeated
failures; if no proxy is usable, downloads fall back to a direct connection. When
rotation is on, connection/SSL/proxy errors and IP bot-challenges are retried so
the job cycles to a fresh IP.

> ⚠️ **Free proxies are unreliable, slow, and a security risk** — many are dead,
> some log or tamper with traffic. **Never** send credentials through them, and
> expect failures. For serious, stable large-volume work, use a **paid
> residential/mobile** list via `--proxy-source` (or the Proxy sources box).

---

## 6. Cookies (for login-required content)

Some content (private posts, age-gated videos, or YouTube's "confirm you're not
a bot" challenge) needs a logged-in session. Two ways to provide it:

- **Cookies from browser** (easiest): in Settings choose `chrome`/`firefox`/`edge`,
  or on the CLI `--cookies-from-browser chrome`. yt-dlp reads cookies from that
  browser's profile.
- **Cookies file**: export a Netscape-format `cookies.txt` (via a browser
  extension) and load it in Settings → **Cookies file**, or `--cookies cookies.txt`.

---

## 7. Resume & where files go

- **Output layout:** `‹output›/‹platform›/‹uploader›/‹title› [id].ext`
  (e.g. `downloads/youtube/jawed/Me at the zoo [jNQXAC9IVRw].webm`).
- **Checkpoint/resume:** each finished URL is recorded in
  `‹output›/.bmd_checkpoint.json` (keyed by a hash of the URL). Re-running the
  same batch **skips** what's already done. Use `--no-resume` to force a full
  re-download.
- **Logs:** written to `‹output›/bmd.log` (and echoed to the console in CLI mode).

---

## 8. Build a standalone app (no Python needed)

To hand the app to someone without Python, build a single executable with
PyInstaller.

**Windows** (produces `dist\BulkMediaDownloader.exe`, opens the GUI on double-click):
```powershell
pip install -r requirements-dev.txt
build.bat
```

Ship `ffmpeg.exe` next to the `.exe` for best video quality. On macOS/Linux you
can build a native binary by running the same PyInstaller command inside
`build.bat` (drop the `--windowed` flag if you want it to run in a terminal).

---

## 9. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `GUI mode requires PySide6…` | Run `pip install -r requirements.txt` in the active venv, or use CLI mode. |
| `yt-dlp is not installed` / `gallery-dl is not installed` | Same — install requirements into the venv you're running. |
| Best quality not downloading / "merging unavailable" warning | Install **ffmpeg** and put it on `PATH` (§2). |
| YouTube: "Sign in to confirm you're not a bot" | That IP is flagged. Provide **cookies** (§6), or enable **proxy rotation** (§5) so it retries on other IPs. |
| Lots of failures with `--proxy-rotate` | Free proxies are flaky by nature. Keep health-check on, raise `--retries`, or switch to a paid list via `--proxy-source`. |
| Everything "Skipped" on re-run | Already downloaded (checkpoint). Use `--no-resume` or a fresh output folder to force re-download. |
| A URL fails with "Unsupported or unrecognized URL" | The link isn't one of the 5 supported platforms, or is malformed. |
| Need more detail on a failure | Set **Log level = DEBUG** (or `--log-level DEBUG`) and check `‹output›/bmd.log`. |

---

## 10. Quick start (copy/paste)

```bash
# 1) install (once)
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2a) run with the UI
python app.py

# 2b) or run a batch from the command line
python app.py --input links.txt --output ./downloads --threads 4
```
