# SOLUTION: Bulk Media Downloader for Multiple Platforms

> **Practical test (Section VII):** Propose a solution to download large amounts of media
> from platforms (Facebook, YouTube, Instagram, TikTok, X) **without being rate-limited or
> blocked by the platforms**, and build software that implements the solution.
>
> **Agreed scope:** A desktop application with a **Windows GUI**, written in **Python**,
> focused on **downloading** (upload is out of scope). Proxy support is planned as a later
> extension.

---

## 1. Solution summary

Rather than writing a custom crawler for each platform — an approach that is expensive to
build and **breaks every time a platform changes** — this solution builds on **two mature,
actively maintained open-source engines** and wraps them with a custom **orchestration
layer + anti-blocking layer + bulk-download GUI**.

| Component | Role |
|---|---|
| **yt-dlp** | Downloads video from YouTube, Facebook, TikTok, X, Instagram (Reels/Video) |
| **gallery-dl** | Downloads images/albums from Instagram, X, Facebook |
| **Custom layer (the deliverable)** | Job queue, multithreading, anti-blocking, GUI, resume, reporting |

**The value of the software lives in the custom layer**, not in downloading a single link:
the ability to download **large volumes reliably without being blocked**, with progress
tracking and the ability to resume after interruptions.

**Why this approach**

- yt-dlp and gallery-dl are community-maintained and updated the moment a platform changes
  its layout or mechanics — we don't have to chase per-platform fixes ourselves.
- They cover all five platforms the test requires, for both video and images.
- They shorten development time so effort can concentrate on the hardest and most heavily
  graded part: **avoiding platform rate limits and blocks**.

---

## 2. High-level architecture

```
   User (Windows GUI)
            │
            ▼
   ┌──────────────────┐      Paste URLs or load a list file (.txt/.csv)
   │   GUI layer       │
   │   (PySide6)      │
   └────────┬─────────┘
            │  URL list + configuration
            ▼
   ┌──────────────────┐
   │  Queue manager    │  Job queue, dispatches work to threads
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐      Caps concurrent threads (total and per platform)
   │   Worker pool     │
   └────────┬─────────┘
            │  each job: one URL
            ▼
   ┌──────────────────┐
   │ Platform detector │  From URL → choose the right engine
   └────────┬─────────┘
     ┌──────┴───────┐
     ▼              ▼
┌─────────┐   ┌───────────┐
│ yt-dlp  │   │ gallery-dl│   ← Wrapped inside the anti-block layer
│ (video) │   │ (images)  │
└────┬────┘   └─────┬─────┘
     └──────┬───────┘
            ▼
   ┌──────────────────┐      Writes a state file so finished links are skipped next run
   │   Checkpoint      │
   └────────┬─────────┘
            ▼
   Local output folder + Logs / result report
```

---

## 3. Per-job download flow

1. **Pull a URL** from the queue.
2. **Check the checkpoint** — skip if this URL was already downloaded in a previous run.
3. **Detect the platform** from the URL's domain.
4. **Pick the engine**: video → yt-dlp; images/albums → gallery-dl.
5. **Apply the anti-block layer**: random delay, rotate User-Agent, load cookies/proxy if set.
6. **Download** and stream progress percentage back to the GUI in real time.
7. **Handle errors**: on a rate-limit response (HTTP 429/403), back off with increasing
   waits and retry (up to N times).
8. **Write the checkpoint** on success; log failures with the reason.
9. **Update the summary counters**: Total / Done / Failed / Remaining.

---

## 4. ⭐ Anti-blocking layer (the core)

This is the heart of what the test emphasizes: **downloading large volumes without being
limited by the platforms**. Platforms typically block based on: requests firing too fast,
too many requests from one IP, missing login sessions, or "automated client" signals. The
solution addresses each cause:

| Technique | How it works | What it prevents |
|---|---|---|
| **Rate limiting + random delay** | Insert a random pause (e.g. 1–5 s) between requests and cap per-platform download speed | Being flagged as "spam / bot" from bursty requests |
| **Retry with exponential backoff** | On a limit error (429/403), wait 2s → 4s → 8s… then retry | Getting past a platform's temporary limits |
| **Proxy rotation** | Fetch a proxy list (built-in free lists or your own), health-check it, then rotate round-robin; a failing proxy is cooled down and dropped after repeated failures | IP-based blocking |
| **Login cookies** | Load browser cookies to reach content that requires sign-in | "Login required" errors and guest blocking |
| **User-Agent rotation** | Randomize the User-Agent string per request | A uniform, easily fingerprinted client |
| **Per-platform thread caps** | e.g. at most 2 concurrent threads per platform | Hammering a single platform at once |
| **Checkpoint & resume** | Record completed URLs; skip them automatically next run | Losing progress when a large batch is interrupted |

**Flexible configuration:** the user can tune thread count, delays, proxy on/off, and the
cookies file — to balance **speed** against **block-safety** per platform.

> **Responsible-use note:** The software targets content the user is entitled to download
> (their own content, licensed content, or personal/research use). The techniques above aim
> to **keep large-volume downloads stable**, not to bypass any security or authentication
> mechanism without authorization. Users are responsible for complying with each platform's
> terms of service and applicable law.

---

## 5. Software features

1. **Bulk input**: paste many URLs (one per line) or load from a `.txt`/`.csv` file.
2. **Automatic platform detection** and routing to the correct engine.
3. **Controlled multithreaded downloading** — configurable thread count.
4. **Anti-blocking layer** (Section 4).
5. **Queue + progress table**: each job shows Waiting / Downloading (%) / Done / Failed.
6. **Resume**: skip already-downloaded files via the checkpoint.
7. **Configuration**: output folder, threads, delay, cookies, proxy rotation, quality.
8. **Logs & report**: export the list of successful / failed results with reasons.
9. **Runs from the command line too**: `python app.py --input links.txt --output D:\media --threads 4`
   (satisfying the "run directly from PowerShell/CMD" requirement).

---

## 6. GUI mockup

```
┌────────────────────────────────────────────────────────────┐
│  Bulk Media Downloader                          [⚙ Settings]│
├────────────────────────────────────────────────────────────┤
│  Paste URLs (one per line):        [📂 Import from file...] │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ https://youtube.com/...                              │  │
│  │ https://tiktok.com/...                               │  │
│  └──────────────────────────────────────────────────────┘  │
│  Save to: [D:\Downloads\media        ] [Browse...]        │
│  Threads: [4 ▾]   Proxy: [☑ Rotate]   Cookies: [Choose...] │
│                                   [ ▶ START DOWNLOAD ]     │
├────────────────────────────────────────────────────────────┤
│  #  │ Platform │ Title          │ Status     │ Progress    │
│  1  │ YouTube  │ Video ABC      │ Downloading│ ███░░ 62%   │
│  2  │ TikTok   │ Clip XYZ       │ ✅ Done    │ 100%        │
│  3  │ Instagram│ Album (10 img) │ ⏳ Waiting │ -           │
├────────────────────────────────────────────────────────────┤
│  Total: 150 | Done: 87 | Failed: 2 | Left: 61  [Export log]│
└────────────────────────────────────────────────────────────┘
```

---

## 7. Technology stack

| Item | Choice | Reason |
|---|---|---|
| Language | **Python 3.11+** | Integrates yt-dlp/gallery-dl directly, fast to build |
| GUI | **PySide6 (Qt)** | Polished UI, good threading, packageable into `.exe` |
| Download engines | **yt-dlp**, **gallery-dl** | Most capable, continuously updated, cover all 5 platforms |
| Concurrency | **QThreadPool** / `concurrent.futures` | Runs in parallel without freezing the GUI |
| Packaging | **PyInstaller** | Produces a single Windows `.exe`, no Python install needed |

---

## 8. Project structure (planned)

```
bulk-media-downloader/
├── app.py                 # Entry point: launches the GUI (or CLI mode)
├── core/
│   ├── downloader.py      # Wraps yt-dlp / gallery-dl
│   ├── platform.py        # Detects the platform from a URL
│   ├── queue_manager.py   # Job queue + multithreading
│   ├── anti_block.py      # Random delay, retry, User-Agent, proxy rotation
│   ├── proxy_pool.py      # Free-proxy fetch, health-check, rotation, cooldown
│   └── checkpoint.py      # State persistence & resume
├── ui/
│   ├── main_window.py     # Main window
│   └── settings_dialog.py # Configuration dialog
├── docs/
│   ├── GUIDE.md           # Step-by-step user guide
│   ├── SOLUTION.md        # Solution document — this file
│   └── SPEC.md            # Technical specification
├── requirements.txt       # Dependencies
├── README.md              # Install & usage guide
└── build.bat              # Command to package into .exe
```

---

## 9. Implementation plan (2 days)

- **Day 1** — Core: downloader, platform detection, multithreaded queue, anti-block layer,
  checkpoint. Tested through CLI mode.
- **Day 2** — PySide6 GUI, wiring to the core, resume, logging/reporting, `.exe` packaging,
  README, real-world testing across all 5 platforms.

---

## 10. How to run (planned)

- **Regular users:** open `BulkMediaDownloader.exe` → use the GUI.
- **Technical users:** run directly from the command line, e.g.:

  ```powershell
  python app.py --input links.txt --output D:\media --threads 4
  ```

Both paths share the same core, satisfying the requirement to "build a Windows UI **or**
run directly from PowerShell/CMD".
