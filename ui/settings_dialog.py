"""Settings dialog (SPEC section 4.8).

A modal dialog that edits the persisted :class:`core.config.Settings`. ``OK``
validates and saves via ``QSettings``; ``Cancel`` discards.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from core.config import BROWSER_CHOICES, LOG_LEVELS, QUALITY_CHOICES, Settings


class SettingsDialog(QDialog):
    """Edit and persist application settings."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        self._settings = settings
        self._build_ui()
        self._load(settings)

    # ----- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        form = QFormLayout()

        self.output_edit = QLineEdit()
        browse_out = QPushButton("Browse…")
        browse_out.clicked.connect(self._browse_output)
        form.addRow("Output folder", _with_button(self.output_edit, browse_out))

        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 16)
        form.addRow("Thread count", self.threads_spin)

        self.per_platform_spin = QSpinBox()
        self.per_platform_spin.setRange(1, 8)
        form.addRow("Per-platform cap", self.per_platform_spin)

        self.delay_min_spin = QDoubleSpinBox()
        self.delay_min_spin.setRange(0.0, 60.0)
        self.delay_min_spin.setSingleStep(0.5)
        form.addRow("Delay min (s)", self.delay_min_spin)

        self.delay_max_spin = QDoubleSpinBox()
        self.delay_max_spin.setRange(0.0, 60.0)
        self.delay_max_spin.setSingleStep(0.5)
        form.addRow("Delay max (s)", self.delay_max_spin)

        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 10)
        form.addRow("Max retries", self.retries_spin)

        self.cookies_edit = QLineEdit()
        browse_cookies = QPushButton("Browse…")
        browse_cookies.clicked.connect(self._browse_cookies)
        form.addRow("Cookies file", _with_button(self.cookies_edit, browse_cookies))

        self.browser_combo = QComboBox()
        self.browser_combo.addItems([b or "none" for b in BROWSER_CHOICES])
        form.addRow("Cookies from browser", self.browser_combo)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(list(QUALITY_CHOICES))
        form.addRow("Quality", self.quality_combo)

        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(0, 1_000_000)
        self.rate_spin.setSuffix(" KB/s")
        form.addRow("Rate limit (0=off)", self.rate_spin)

        self.proxy_enabled_check = QCheckBox()
        self.proxy_enabled_check.setToolTip(
            "Route downloads through a proxy (single address or rotation).")
        form.addRow("Proxy enabled", self.proxy_enabled_check)

        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("http://ip:port (single proxy, optional)")
        form.addRow("Proxy address", self.proxy_edit)

        self.proxy_rotate_check = QCheckBox()
        self.proxy_rotate_check.setToolTip(
            "Rotate through many free public proxies (unreliable/slow; see README).")
        form.addRow("Rotate free proxies", self.proxy_rotate_check)

        self.proxy_validate_check = QCheckBox()
        self.proxy_validate_check.setToolTip(
            "Health-check proxies before use — recommended (slower to start).")
        form.addRow("Health-check proxies", self.proxy_validate_check)

        self.proxy_sources_edit = QPlainTextEdit()
        self.proxy_sources_edit.setPlaceholderText(
            "Optional: one proxy-list URL per line. Leave empty to use built-in "
            "free sources. Paste paid/residential list URLs here for reliability.")
        self.proxy_sources_edit.setFixedHeight(60)
        form.addRow("Proxy sources", self.proxy_sources_edit)

        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(list(LOG_LEVELS))
        form.addRow("Log level", self.log_level_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    # ----- load / save -----------------------------------------------------

    def _load(self, s: Settings) -> None:
        self.output_edit.setText(s.output_dir)
        self.threads_spin.setValue(s.threads)
        self.per_platform_spin.setValue(s.per_platform)
        self.delay_min_spin.setValue(s.delay_min)
        self.delay_max_spin.setValue(s.delay_max)
        self.retries_spin.setValue(s.retries)
        self.cookies_edit.setText(s.cookies_file)
        self.browser_combo.setCurrentText(s.cookies_from_browser or "none")
        self.quality_combo.setCurrentText(s.quality)
        self.rate_spin.setValue(s.rate_limit_kbps)
        self.proxy_enabled_check.setChecked(s.proxy_enabled)
        self.proxy_edit.setText(s.proxy)
        self.proxy_rotate_check.setChecked(s.proxy_rotate)
        self.proxy_validate_check.setChecked(s.proxy_validate)
        self.proxy_sources_edit.setPlainText(s.proxy_sources)
        self.log_level_combo.setCurrentText(s.log_level)

    def _on_accept(self) -> None:
        if self.delay_max_spin.value() < self.delay_min_spin.value():
            QMessageBox.warning(
                self, "Invalid delays",
                "Delay max must be >= delay min; values will be swapped.")
        s = self._settings
        s.output_dir = self.output_edit.text().strip() or "./downloads"
        s.threads = self.threads_spin.value()
        s.per_platform = self.per_platform_spin.value()
        s.delay_min = self.delay_min_spin.value()
        s.delay_max = self.delay_max_spin.value()
        s.retries = self.retries_spin.value()
        s.cookies_file = self.cookies_edit.text().strip()
        browser = self.browser_combo.currentText()
        s.cookies_from_browser = "" if browser == "none" else browser
        s.quality = self.quality_combo.currentText()
        s.rate_limit_kbps = self.rate_spin.value()
        s.proxy_enabled = self.proxy_enabled_check.isChecked()
        s.proxy = self.proxy_edit.text().strip()
        s.proxy_rotate = self.proxy_rotate_check.isChecked()
        s.proxy_validate = self.proxy_validate_check.isChecked()
        s.proxy_sources = self.proxy_sources_edit.toPlainText().strip()
        # Rotation and a single proxy both imply the proxy layer is enabled.
        if s.proxy_rotate or s.proxy:
            s.proxy_enabled = True
        s.log_level = self.log_level_combo.currentText()
        s.validate()
        s.save_to_qsettings()
        self.accept()

    def settings(self) -> Settings:
        """Return the (possibly edited) settings instance."""
        return self._settings

    # ----- browse helpers --------------------------------------------------

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if path:
            self.output_edit.setText(path)

    def _browse_cookies(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose cookies file", "", "Cookies (*.txt);;All files (*)")
        if path:
            self.cookies_edit.setText(path)


def _with_button(edit: QLineEdit, button: QPushButton) -> QWidget:
    """Wrap a line edit and a button in a horizontal container widget."""
    container = QWidget()
    row = QHBoxLayout(container)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(edit)
    row.addWidget(button)
    return container
