"""Job queue, worker pool, and per-platform concurrency (SPEC section 4.5).

This module owns the :class:`Job` / :class:`JobStatus` data model and the
:class:`QueueManager`. It emits events through plain callbacks and never imports
Qt, so the same core drives both the CLI and (via a bridge) the GUI. All heavy
sibling modules are imported only under ``TYPE_CHECKING`` to avoid import
cycles; the concrete collaborators are injected in the constructor.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Iterable, Optional

from core.anti_block import CancelledError
from core.platform import Platform, detect_platform, normalize_url

if TYPE_CHECKING:
    from core.anti_block import AntiBlock
    from core.checkpoint import CheckpointStore
    from core.config import Settings
    from core.downloader import Downloader, ProgressEvent

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Lifecycle states of a download job."""

    QUEUED = "queued"        # in queue, not yet started
    RUNNING = "running"      # actively downloading
    DONE = "done"            # completed successfully
    FAILED = "failed"        # exhausted retries or fatal error
    SKIPPED = "skipped"      # already in checkpoint (resume)
    CANCELLED = "cancelled"  # user stopped the queue before it ran


_TERMINAL = {JobStatus.DONE, JobStatus.FAILED, JobStatus.SKIPPED, JobStatus.CANCELLED}


@dataclass
class Job:
    """A single download unit tracked by the :class:`QueueManager`."""

    id: int
    url: str
    platform: Platform
    status: JobStatus = JobStatus.QUEUED
    title: str = ""
    progress: float = 0.0
    speed: str = ""
    eta: str = ""
    output_path: str = ""
    error: str = ""
    attempts: int = 0
    url_hash: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def __post_init__(self) -> None:
        if not self.url_hash:
            self.url_hash = hashlib.sha256(self.url.encode("utf-8")).hexdigest()


def copy_job(job: Job) -> Job:
    """Return a shallow copy of ``job`` (used when emitting to the GUI thread)."""
    return Job(
        id=job.id, url=job.url, platform=job.platform, status=job.status,
        title=job.title, progress=job.progress, speed=job.speed, eta=job.eta,
        output_path=job.output_path, error=job.error, attempts=job.attempts,
        url_hash=job.url_hash, created_at=job.created_at,
        finished_at=job.finished_at,
    )


@dataclass
class QueueSummary:
    """Aggregate counters over all jobs."""

    total: int
    done: int
    failed: int
    skipped: int
    running: int
    remaining: int


JobEventCallback = Callable[[Job], None]


class QueueManager:
    """Owns jobs, a thread pool, and per-platform concurrency semaphores."""

    def __init__(
        self,
        settings: "Settings",
        downloader: "Downloader",
        anti_block: "AntiBlock",
        checkpoint: "CheckpointStore",
    ) -> None:
        self.settings = settings
        self.downloader = downloader
        self.anti_block = anti_block
        self.checkpoint = checkpoint

        self._jobs: list[Job] = []
        self._listeners: list[JobEventCallback] = []
        self._next_id = 1

        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: list[Future] = []
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # One semaphore per platform, initialized to the per-platform cap.
        self._semaphores: dict[Platform, threading.Semaphore] = {
            platform: threading.Semaphore(max(1, settings.per_platform))
            for platform in Platform
        }

    # ----- job construction ------------------------------------------------

    def add_url(self, url: str) -> Job:
        """Create and register a :class:`Job` for a single URL."""
        normalized = normalize_url(url)
        platform = detect_platform(normalized)
        with self._lock:
            job = Job(id=self._next_id, url=normalized, platform=platform)
            self._next_id += 1
            self._jobs.append(job)
        return job

    def add_urls(self, urls: Iterable[str]) -> list[Job]:
        """Create jobs for many URLs, deduping while preserving order."""
        seen: set[str] = {j.url for j in self._jobs}
        created: list[Job] = []
        for raw in urls:
            normalized = normalize_url(raw)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            created.append(self.add_url(normalized))
        return created

    # ----- listeners -------------------------------------------------------

    def on_job_event(self, cb: JobEventCallback) -> None:
        """Register a listener invoked on any job state/progress change.

        Register all listeners before :meth:`start`; the list is treated as
        read-only afterward.
        """
        self._listeners.append(cb)

    def _emit(self, job: Job) -> None:
        """Notify all listeners about ``job`` (called on worker threads)."""
        for cb in self._listeners:
            try:
                cb(job)
            except Exception:  # noqa: BLE001 - a bad listener must not kill work
                logger.exception("job listener raised")

    # ----- run control -----------------------------------------------------

    def start(self) -> None:
        """Submit all QUEUED jobs to the pool. Non-blocking."""
        self._stop_event.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, self.settings.threads),
            thread_name_prefix="bmd-worker",
        )
        with self._lock:
            queued = [j for j in self._jobs if j.status is JobStatus.QUEUED]
        self._futures = [self._executor.submit(self._run_job, job) for job in queued]
        # Allow the pool to shut down once all submitted work drains.
        self._executor.shutdown(wait=False)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until all submitted jobs are terminal. Returns True if finished."""
        if not self._futures:
            return True
        done, not_done = futures_wait(self._futures, timeout=timeout)
        return not not_done

    def stop(self) -> None:
        """Request cancellation: running jobs abort, QUEUED jobs become CANCELLED."""
        self._stop_event.set()
        with self._lock:
            pending = [j for j in self._jobs if j.status is JobStatus.QUEUED]
        for job in pending:
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
            self._emit(job)

    # ----- worker ----------------------------------------------------------

    def _run_job(self, job: Job) -> None:
        """Execute one job end to end on a pool thread (SPEC 4.5 routine)."""
        try:
            if self._stop_event.is_set():
                self._finish(job, JobStatus.CANCELLED)
                return

            if job.platform is Platform.UNKNOWN:
                self._finish(job, JobStatus.FAILED,
                             error="Unsupported or unrecognized URL")
                return

            if self.settings.resume and self.checkpoint.is_done(job.url_hash):
                self._finish(job, JobStatus.SKIPPED)
                return

            job.status = JobStatus.RUNNING
            job.progress = 0.0
            self._emit(job)

            cancel_check = self._stop_event.is_set
            semaphore = self._semaphores[job.platform]
            semaphore.acquire()
            try:
                result = self.anti_block.run_with_retry(
                    attempt_fn=lambda n: self.downloader.download(
                        job, self._make_progress_cb(job), cancel_check),
                    job=job,
                    cancel_check=cancel_check,
                )
            finally:
                semaphore.release()

            if self._stop_event.is_set() and not result.ok:
                self._finish(job, JobStatus.CANCELLED)
                return

            if result.ok:
                job.title = result.title or job.title
                job.output_path = result.output_path or job.output_path
                job.progress = 100.0
                job.finished_at = time.time()
                job.status = JobStatus.DONE
                self.checkpoint.mark_done(job)
                self._emit(job)
                logger.info("job %d DONE: %s", job.id, job.output_path)
            else:
                self._finish(job, JobStatus.FAILED,
                             error=result.error or "Download failed")
        except CancelledError:
            self._finish(job, JobStatus.CANCELLED)
        except Exception as exc:  # noqa: BLE001 - never let a worker kill the pool
            logger.error("job %d crashed: %s", job.id, exc, exc_info=True)
            self._finish(job, JobStatus.FAILED, error=str(exc))

    def _make_progress_cb(self, job: Job) -> Callable[["ProgressEvent"], None]:
        """Build a progress callback that mutates ``job`` and emits updates."""
        def on_progress(event: "ProgressEvent") -> None:
            if event.title:
                job.title = event.title
            job.progress = event.progress
            if event.speed:
                job.speed = event.speed
            if event.eta:
                job.eta = event.eta
            if event.output_path:
                job.output_path = event.output_path
            # Keep the job RUNNING during progress; terminal state is set by
            # _run_job so checkpointing stays authoritative.
            if job.status is not JobStatus.RUNNING:
                job.status = JobStatus.RUNNING
            self._emit(job)
        return on_progress

    def _finish(self, job: Job, status: JobStatus, error: str = "") -> None:
        """Set a terminal status, stamp the finish time, log, and emit."""
        job.status = status
        job.error = error
        job.finished_at = time.time()
        if status is JobStatus.FAILED and error:
            logger.error(
                "job %d FAILED (%s attempts=%d): %s",
                job.id, job.platform.value, job.attempts, error,
            )
        self._emit(job)

    # ----- introspection ---------------------------------------------------

    @property
    def jobs(self) -> list[Job]:
        """Return the live job list (UI/CLI must only read)."""
        return self._jobs

    def summary(self) -> QueueSummary:
        """Compute aggregate counters under the lock."""
        with self._lock:
            total = len(self._jobs)
            done = sum(1 for j in self._jobs if j.status is JobStatus.DONE)
            failed = sum(1 for j in self._jobs if j.status is JobStatus.FAILED)
            skipped = sum(1 for j in self._jobs if j.status is JobStatus.SKIPPED)
            running = sum(1 for j in self._jobs if j.status is JobStatus.RUNNING)
            remaining = sum(1 for j in self._jobs if j.status not in _TERMINAL)
        return QueueSummary(total=total, done=done, failed=failed,
                            skipped=skipped, running=running, remaining=remaining)
