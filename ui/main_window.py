"""Main application window (SPEC section 4.7).

Contains the URL input area, config row, jobs table (a custom
:class:`QAbstractTableModel` + progress-bar delegate), and a status bar. A
:class:`QueueBridge` marshals worker-thread events onto the GUI thread via Qt
signals (``AutoConnection``).
"""

from __future__ import annotations

import csv
import logging
import os

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QObject, QRectF, Qt, Signal, Slot,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QStyledItemDelegate, QTableView, QVBoxLayout,
    QWidget,
)

from core.anti_block import AntiBlock
from core.checkpoint import CheckpointStore
from core.config import Settings
from core.downloader import Downloader
from core.queue_manager import (
    Job, JobStatus, QueueManager, QueueSummary, copy_job,
)
from ui import theme
from ui.settings_dialog import SettingsDialog

logger = logging.getLogger(__name__)

_COLUMNS = ["#", "Platform", "Title", "Status", "Progress", "Speed/ETA"]

_STATUS_ICON = {
    JobStatus.QUEUED: "⏳",
    JobStatus.RUNNING: "⬇",
    JobStatus.DONE: "✅",
    JobStatus.FAILED: "❌",
    JobStatus.SKIPPED: "⏭",
    JobStatus.CANCELLED: "🚫",
}


class QueueBridge(QObject):
    """Re-emits worker-thread job events as Qt signals for the GUI thread."""

    job_updated = Signal(object)      # emits a Job snapshot

    def on_job_event(self, job: Job) -> None:
        """Worker-thread callback: emit a snapshot; Qt queues it to the GUI."""
        self.job_updated.emit(copy_job(job))


class JobsTableModel(QAbstractTableModel):
    """Table model backed by a list of :class:`Job` snapshots."""

    def __init__(self) -> None:
        super().__init__()
        self._jobs: list[Job] = []
        self._index_by_id: dict[int, int] = {}

    def set_jobs(self, jobs: list[Job]) -> None:
        """Replace all rows with fresh snapshots."""
        self.beginResetModel()
        self._jobs = [copy_job(j) for j in jobs]
        self._index_by_id = {j.id: i for i, j in enumerate(self._jobs)}
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._jobs)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(_COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return _COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        job = self._jobs[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return job.id
            if col == 1:
                return job.platform.value.title()
            if col == 2:
                return job.title or job.url
            if col == 3:
                return f"{_STATUS_ICON.get(job.status, '')} {job.status.value.title()}"
            if col == 4:
                return job.progress  # rendered by the delegate
            if col == 5:
                parts = [p for p in (job.speed, job.eta) if p]
                return " / ".join(parts)
        if role == Qt.TextAlignmentRole and col == 0:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and col == 3 and job.error:
            return job.error
        return None

    @Slot(object)
    def update_job(self, job: Job) -> None:
        """Slot: update or append a single job row (GUI thread)."""
        row = self._index_by_id.get(job.id)
        if row is None:
            row = len(self._jobs)
            self.beginInsertRows(QModelIndex(), row, row)
            self._jobs.append(copy_job(job))
            self._index_by_id[job.id] = row
            self.endInsertRows()
            return
        self._jobs[row] = copy_job(job)
        top = self.index(row, 0)
        bottom = self.index(row, len(_COLUMNS) - 1)
        self.dataChanged.emit(top, bottom)

    def progress_for(self, row: int) -> float:
        return self._jobs[row].progress if 0 <= row < len(self._jobs) else 0.0


class ProgressBarDelegate(QStyledItemDelegate):
    """Draws a rounded progress pill; colours follow the live theme palette."""

    def paint(self, painter, option, index):
        progress = index.data(Qt.DisplayRole)
        try:
            value = max(0, min(100, int(float(progress))))
        except (TypeError, ValueError):
            value = 0

        p = theme.palette()
        rect = QRectF(option.rect).adjusted(6, 7, -6, -7)
        radius = rect.height() / 2

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Track
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(p["TRACK"]))
        painter.drawRoundedRect(rect, radius, radius)

        # Fill
        if value > 0:
            fill = QRectF(rect)
            fill.setWidth(rect.width() * value / 100.0)
            colour = p["DONE"] if value >= 100 else p["ACCENT"]
            painter.setBrush(QColor(colour))
            painter.drawRoundedRect(fill, radius, radius)

        # Centred percentage label. Use white over the coloured fill once it is
        # wide enough to sit under the text, otherwise the muted body text.
        painter.setPen(QColor("#ffffff") if value >= 45 else QColor(p["TEXT"]))
        painter.drawText(option.rect, Qt.AlignCenter, f"{value}%")
        painter.restore()


def _card() -> QFrame:
    """A bordered, rounded surface used to group related controls."""
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def _divider() -> QFrame:
    """A thin vertical separator for the config row (styled via QSS)."""
    line = QFrame()
    line.setObjectName("vDivider")
    line.setFixedWidth(1)
    return line


class MainWindow(QWidget):
    """Top-level window wiring the GUI to the Qt-free core."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.queue: QueueManager | None = None
        self.bridge = QueueBridge()
        self._running = False

        self.setWindowTitle("Bulk Media Downloader")
        self.setObjectName("root")
        self.resize(960, 680)
        self.setMinimumSize(720, 560)
        self._build_ui()

        self.bridge.job_updated.connect(self.model.update_job)
        self.bridge.job_updated.connect(self._on_any_job_update)

        # Live-follow the OS colour scheme while the user hasn't pinned a choice.
        self._subscribe_os_theme()

    def _subscribe_os_theme(self) -> None:
        """Connect to the OS colour-scheme signal (Qt 6.5+); no-op if absent."""
        app = QApplication.instance()
        if app is None:
            return
        try:
            app.styleHints().colorSchemeChanged.connect(self._on_os_theme_changed)
        except (AttributeError, RuntimeError):  # pragma: no cover - older Qt
            pass

    # ----- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        # 1. Header row -----------------------------------------------------
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("Bulk Media Downloader")
        title.setObjectName("appTitle")
        subtitle = QLabel("YouTube · Facebook · Instagram · TikTok · X")
        subtitle.setObjectName("appSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("ghostButton")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setToolTip("Switch between light and dark mode")
        self.theme_btn.clicked.connect(self._toggle_theme)
        self._refresh_theme_button()
        header.addWidget(self.theme_btn, 0, Qt.AlignTop)
        settings_btn = QPushButton("⚙  Settings")
        settings_btn.setObjectName("ghostButton")
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.clicked.connect(self._open_settings)
        header.addWidget(settings_btn, 0, Qt.AlignTop)
        layout.addLayout(header)

        # 2. URL input card -------------------------------------------------
        url_card = _card()
        url_lay = QVBoxLayout(url_card)
        url_lay.setContentsMargins(16, 14, 16, 16)
        url_lay.setSpacing(10)

        url_row = QHBoxLayout()
        url_label = QLabel("Links")
        url_label.setObjectName("sectionLabel")
        url_row.addWidget(url_label)
        url_row.addStretch(1)
        import_btn = QPushButton("📂  Import from file…")
        import_btn.setCursor(Qt.PointingHandCursor)
        import_btn.clicked.connect(self._import_file)
        url_row.addWidget(import_btn)
        url_lay.addLayout(url_row)

        self.url_input = QPlainTextEdit()
        self.url_input.setPlaceholderText(
            "Paste one URL per line…\nhttps://youtube.com/watch?v=…")
        self.url_input.setFixedHeight(128)
        url_lay.addWidget(self.url_input)
        layout.addWidget(url_card)

        # 3. Config card ----------------------------------------------------
        config_card = _card()
        config = QHBoxLayout(config_card)
        config.setContentsMargins(16, 12, 16, 12)
        config.setSpacing(10)

        config.addWidget(QLabel("Save to"))
        self.output_edit = QLineEdit(self.settings.output_dir)
        config.addWidget(self.output_edit, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.clicked.connect(self._browse_output)
        config.addWidget(browse_btn)

        config.addWidget(_divider())

        config.addWidget(QLabel("Threads"))
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 16)
        self.threads_spin.setValue(self.settings.threads)
        config.addWidget(self.threads_spin)

        self.cookies_btn = QPushButton("🍪  Cookies…")
        self.cookies_btn.setCursor(Qt.PointingHandCursor)
        self.cookies_btn.clicked.connect(self._choose_cookies)
        config.addWidget(self.cookies_btn)

        self.proxy_check = QCheckBox("Proxy rotation")
        self.proxy_check.setChecked(
            self.settings.proxy_enabled and self.settings.proxy_rotate)
        self.proxy_check.setToolTip(
            "Rotate through free public proxies (unreliable/slow; see README). "
            "Configure sources & health-check in Settings.")
        config.addWidget(self.proxy_check)
        layout.addWidget(config_card)

        # 4. Primary action -------------------------------------------------
        self.start_btn = QPushButton("▶   START DOWNLOAD")
        self.start_btn.setObjectName("primaryButton")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setMinimumHeight(46)
        self.start_btn.clicked.connect(self._toggle_start)
        layout.addWidget(self.start_btn)

        # 5. Jobs table -----------------------------------------------------
        self.model = JobsTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setItemDelegateForColumn(4, ProgressBarDelegate(self.table))
        header_view = self.table.horizontalHeader()
        header_view.setHighlightSections(False)
        header_view.setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        # 6. Status bar -----------------------------------------------------
        status = QHBoxLayout()
        self.status_label = QLabel("Total: 0   ·   Done: 0   ·   Failed: 0   ·   Left: 0")
        self.status_label.setProperty("muted", "true")
        status.addWidget(self.status_label)
        status.addStretch(1)
        export_btn = QPushButton("⬇  Export log…")
        export_btn.setObjectName("ghostButton")
        export_btn.setCursor(Qt.PointingHandCursor)
        export_btn.clicked.connect(self._export_log)
        status.addWidget(export_btn)
        layout.addLayout(status)

    # ----- settings & config helpers --------------------------------------

    def _refresh_theme_button(self) -> None:
        """Label the toggle with the mode it will switch *to*."""
        if theme.current_mode() == "dark":
            self.theme_btn.setText("☀  Light")
        else:
            self.theme_btn.setText("🌙  Dark")

    def _apply_mode(self, mode: str) -> None:
        """Cross-fade the whole window from its current look to ``mode``."""
        app = QApplication.instance()
        if app is None:
            return

        def swap() -> None:
            theme.apply(app, mode)
            self._refresh_theme_button()
            # Reapplying the app stylesheet restyles children; nudge the table so
            # the hand-painting progress delegate repaints in the new palette.
            self.table.viewport().update()

        self._fade_swap(swap)

    def _fade_swap(self, apply_fn) -> None:
        """Snapshot the window, apply ``apply_fn``, then fade the snapshot out."""
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel

        snapshot = self.grab()
        overlay = QLabel(self)
        overlay.setPixmap(snapshot)
        overlay.setGeometry(self.rect())
        overlay.show()
        overlay.raise_()

        apply_fn()  # switch the theme underneath the frozen snapshot

        effect = QGraphicsOpacityEffect(overlay)
        overlay.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(260)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.finished.connect(overlay.deleteLater)
        anim.start()
        self._theme_anim = anim  # keep a reference so it isn't GC'd

    def _toggle_theme(self) -> None:
        # A manual toggle pins an explicit preference (stops OS-following).
        target = "light" if theme.current_mode() == "dark" else "dark"
        theme.set_preference(target)
        self._apply_mode(target)

    @Slot()
    def _on_os_theme_changed(self) -> None:
        """Follow the OS scheme live — only while no manual choice is pinned."""
        app = QApplication.instance()
        if app is None or theme.preference() != theme.SYSTEM:
            return
        resolved = theme.os_mode(app)
        if resolved != theme.current_mode():
            self._apply_mode(resolved)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.settings()
            self.output_edit.setText(self.settings.output_dir)
            self.threads_spin.setValue(self.settings.threads)

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if path:
            self.output_edit.setText(path)

    def _choose_cookies(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose cookies file", "", "Cookies (*.txt);;All files (*)")
        if path:
            self.settings.cookies_file = path
            self.cookies_btn.setText(f"Cookies: {os.path.basename(path)}")

    def _import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import URL list", "", "Text/CSV (*.txt *.csv);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                lines = [ln.strip() for ln in fh
                         if ln.strip() and not ln.strip().startswith("#")]
        except OSError as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        existing = self.url_input.toPlainText().rstrip()
        joined = "\n".join(line.split(",")[0].strip() for line in lines)
        self.url_input.setPlainText((existing + "\n" + joined).strip())

    # ----- start / stop ----------------------------------------------------

    def _collect_settings(self) -> Settings:
        """Mirror the config-row widgets back into the settings object."""
        self.settings.output_dir = self.output_edit.text().strip() or "./downloads"
        self.settings.threads = self.threads_spin.value()
        rotate = self.proxy_check.isChecked()
        self.settings.proxy_enabled = rotate or self.settings.proxy_enabled
        self.settings.proxy_rotate = rotate
        return self.settings.validate()

    def _build_proxy_pool(self, settings: Settings):
        """Fetch/validate the proxy pool with a wait cursor, or return None.

        Runs inline on the GUI thread; free-list fetch + health-check may take a
        few seconds, so we show a busy cursor. Kept simple by design.
        """
        if not (settings.proxy_enabled and settings.proxy_rotate):
            return None
        from core.proxy_pool import build_pool_from_settings
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pool = build_pool_from_settings(settings)
        except Exception as exc:  # noqa: BLE001 - never block starting on this
            logger.warning("proxy pool build failed: %s", exc)
            pool = None
        finally:
            QApplication.restoreOverrideCursor()
        if pool is not None:
            usable = pool.available
            self.status_label.setText(
                f"Proxy pool: {usable} usable proxy(ies)")
            if usable == 0:
                QMessageBox.warning(
                    self, "No proxies",
                    "Proxy rotation is on but no usable proxies were found.\n"
                    "Downloads will use a direct connection.")
        return pool

    def _set_start_mode(self, running: bool) -> None:
        """Toggle the primary button between the START and STOP appearances."""
        if running:
            self.start_btn.setText("■   STOP")
            self.start_btn.setObjectName("dangerButton")
        else:
            self.start_btn.setText("▶   START DOWNLOAD")
            self.start_btn.setObjectName("primaryButton")
        # Re-evaluate the stylesheet now that objectName changed.
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)

    def _toggle_start(self) -> None:
        if self._running:
            self._stop_queue()
        else:
            self._start_queue()

    def _start_queue(self) -> None:
        urls = [ln.strip() for ln in self.url_input.toPlainText().splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
        if not urls:
            QMessageBox.information(self, "No URLs", "Paste at least one URL.")
            return

        settings = self._collect_settings()
        os.makedirs(settings.output_dir, exist_ok=True)

        proxy_pool = self._build_proxy_pool(settings)
        anti_block = AntiBlock(settings, proxy_pool=proxy_pool)
        downloader = Downloader(settings, anti_block)
        checkpoint = CheckpointStore(settings.effective_checkpoint_path())
        checkpoint.load()
        self.queue = QueueManager(settings, downloader, anti_block, checkpoint)
        self.queue.add_urls(urls)
        self.queue.on_job_event(self.bridge.on_job_event)

        self.model.set_jobs(self.queue.jobs)
        self._update_status()

        self.queue.start()
        self._running = True
        self.start_btn.setEnabled(True)
        self._set_start_mode(running=True)
        # Single authoritative completion detector. Job events can't be trusted
        # to reset the button: a worker emits its terminal event *before* its
        # Future resolves, so wait() may still report "not finished" on the last
        # event and no further event ever arrives. Poll the pool instead.
        self._watch_completion(self.queue)

    def _stop_queue(self) -> None:
        if self.queue:
            self.queue.stop()
        self.start_btn.setEnabled(False)
        self.start_btn.setText("Stopping…")
        # The watcher started in _start_queue will reset the button once workers
        # drain — no second watcher needed here.

    def _watch_completion(self, queue: QueueManager) -> None:
        """Poll ``queue`` on the GUI thread until every worker has drained."""
        from PySide6.QtCore import QTimer

        def check() -> None:
            # A newer run may have replaced the queue; let the old watcher die.
            if self.queue is not queue:
                return
            if queue.wait(timeout=0.0):
                self._on_queue_finished()
            else:
                QTimer.singleShot(200, check)

        QTimer.singleShot(200, check)

    def _on_queue_finished(self) -> None:
        """Reset the UI to idle once the active queue has fully drained."""
        self._running = False
        self.start_btn.setEnabled(True)
        self._set_start_mode(running=False)
        self._update_status()

    # ----- event slots -----------------------------------------------------

    @Slot(object)
    def _on_any_job_update(self, job: Job) -> None:
        # Status only. Button reset is owned solely by the completion watcher,
        # because a terminal job event can arrive before its Future resolves.
        self._update_status()

    def _update_status(self) -> None:
        if not self.queue:
            return
        s: QueueSummary = self.queue.summary()
        self.status_label.setText(
            f"Total: {s.total}   ·   Done: {s.done}   ·   "
            f"Failed: {s.failed}   ·   Left: {s.remaining}")

    # ----- export ----------------------------------------------------------

    def _export_log(self) -> None:
        if not self.queue or not self.queue.jobs:
            QMessageBox.information(self, "Nothing to export", "No jobs yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export log", "bmd_results.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["#", "platform", "url", "status", "title", "error", "output_path"])
            for job in self.queue.jobs:
                writer.writerow([
                    job.id, job.platform.value, job.url, job.status.value,
                    job.title, job.error, job.output_path,
                ])
        QMessageBox.information(self, "Exported", f"Log written to:\n{path}")
