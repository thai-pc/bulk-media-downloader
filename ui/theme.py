"""Modern application theme (light + dark) — QSS built from a palette.

Two cohesive themes share one stylesheet template; only the palette differs.
``apply_theme(app, mode)`` installs the stylesheet process-wide, ``toggle_theme``
flips between light and dark, and the choice is persisted via ``QSettings`` so
it survives restarts. The core package never imports this; only the GUI does.

Widgets opt into styling through ``objectName`` hooks referenced below
(e.g. ``#primaryButton``, ``#card``, ``#appTitle``). Delegates that paint by
hand read the live palette via :func:`palette` so they follow theme switches.
"""

from __future__ import annotations

# ---- Palettes ------------------------------------------------------------
# Every key the stylesheet template consumes. Both themes must define all of
# them so switching never leaves a widget unstyled.

DARK: dict[str, str] = {
    "BG": "#0f172a",            # window background (slate-900)
    "SURFACE": "#1e293b",       # card surface (slate-800)
    "SURFACE_ALT": "#182234",   # alternating table row
    "BORDER": "#334155",        # hairline borders (slate-700)
    "TEXT": "#e2e8f0",          # primary text (slate-200)
    "TEXT_MUTED": "#94a3b8",    # secondary text (slate-400)
    "TITLE": "#f8fafc",         # app title
    "ACCENT": "#6366f1",        # indigo-500
    "ACCENT_HOVER": "#818cf8",  # indigo-400
    "ACCENT_2": "#8b5cf6",      # violet-500
    "DANGER": "#ef4444",
    "DANGER_HOVER": "#f87171",
    "DONE": "#22c55e",          # progress complete
    "TRACK": "#0b1220",         # progress-bar track
    "INPUT_BG": "#0b1220",      # text-input background
    "BTN_BG": "#223047",        # secondary button
    "BTN_BG_HOVER": "#2a3a54",
    "BTN_BG_PRESSED": "#1b2740",
    "BTN_DISABLED_BG": "#1a2436",
    "GHOST_HOVER_BG": "#1c2740",
}

LIGHT: dict[str, str] = {
    "BG": "#f1f5f9",            # window background (slate-100)
    "SURFACE": "#ffffff",       # card surface
    "SURFACE_ALT": "#f8fafc",   # alternating table row
    "BORDER": "#e2e8f0",        # hairline borders (slate-200)
    "TEXT": "#1e293b",          # primary text (slate-800)
    "TEXT_MUTED": "#64748b",    # secondary text (slate-500)
    "TITLE": "#0f172a",         # app title
    "ACCENT": "#6366f1",        # indigo-500
    "ACCENT_HOVER": "#4f46e5",  # indigo-600
    "ACCENT_2": "#7c3aed",      # violet-600
    "DANGER": "#ef4444",
    "DANGER_HOVER": "#dc2626",
    "DONE": "#16a34a",          # progress complete
    "TRACK": "#e2e8f0",         # progress-bar track
    "INPUT_BG": "#f8fafc",      # text-input background
    "BTN_BG": "#f1f5f9",        # secondary button
    "BTN_BG_HOVER": "#e8edf5",
    "BTN_BG_PRESSED": "#dde5f0",
    "BTN_DISABLED_BG": "#f1f5f9",
    "GHOST_HOVER_BG": "#eef2f9",
}

_PALETTES = {"dark": DARK, "light": LIGHT}
_DEFAULT_MODE = "dark"

# Live state: the currently applied mode and palette. Delegates read these.
_mode: str = _DEFAULT_MODE
_current: dict[str, str] = DARK


def palette() -> dict[str, str]:
    """Return the palette dict for the currently applied theme."""
    return _current


def current_mode() -> str:
    """Return the currently applied mode name (``"dark"`` or ``"light"``)."""
    return _mode


# ---- Stylesheet template -------------------------------------------------

def _build_qss(p: dict[str, str]) -> str:
    return f"""
* {{
    font-family: "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", Arial;
    font-size: 13px;
    color: {p['TEXT']};
}}

QWidget#root, QDialog {{ background: {p['BG']}; }}

/* ---- Typographic accents ---- */
QLabel#appTitle {{ font-size: 20px; font-weight: 700; color: {p['TITLE']}; }}
QLabel#appSubtitle, QLabel[muted="true"] {{
    color: {p['TEXT_MUTED']}; font-size: 12px;
}}
QLabel#sectionLabel {{
    font-size: 12px; font-weight: 600; color: {p['TEXT_MUTED']};
    text-transform: uppercase; letter-spacing: 1px;
}}

/* ---- Cards ---- */
QFrame#card {{
    background: {p['SURFACE']};
    border: 1px solid {p['BORDER']};
    border-radius: 12px;
}}
QFrame#vDivider {{ background: {p['BORDER']}; max-width: 1px; border: none; }}

/* ---- Text inputs ---- */
QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p['INPUT_BG']};
    border: 1px solid {p['BORDER']};
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: {p['ACCENT']};
    selection-color: #ffffff;
}}
QPlainTextEdit:focus, QLineEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {p['ACCENT']}; }}
QPlainTextEdit {{ padding: 10px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {p['SURFACE']};
    border: 1px solid {p['BORDER']};
    selection-background-color: {p['ACCENT']};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px; border: none; background: transparent;
}}

/* ---- Buttons ---- */
QPushButton {{
    background: {p['BTN_BG']};
    border: 1px solid {p['BORDER']};
    border-radius: 8px;
    padding: 8px 16px;
    color: {p['TEXT']};
    font-weight: 600;
}}
QPushButton:hover {{ background: {p['BTN_BG_HOVER']}; border-color: {p['ACCENT']}; }}
QPushButton:pressed {{ background: {p['BTN_BG_PRESSED']}; }}
QPushButton:disabled {{ color: {p['TEXT_MUTED']}; background: {p['BTN_DISABLED_BG']}; }}

QPushButton#primaryButton {{
    background: {p['ACCENT']}; border: none; color: #ffffff;
    font-size: 14px; font-weight: 700; padding: 12px 20px; letter-spacing: 0.3px;
}}
QPushButton#primaryButton:hover {{ background: {p['ACCENT_HOVER']}; }}
QPushButton#primaryButton:pressed {{ background: {p['ACCENT_2']}; }}

QPushButton#dangerButton {{
    background: {p['DANGER']}; border: none; color: #ffffff;
    font-size: 14px; font-weight: 700; padding: 12px 20px;
}}
QPushButton#dangerButton:hover {{ background: {p['DANGER_HOVER']}; }}

QPushButton#ghostButton {{ background: transparent; border: 1px solid {p['BORDER']}; }}
QPushButton#ghostButton:hover {{
    border-color: {p['ACCENT']}; background: {p['GHOST_HOVER_BG']};
}}

/* ---- Checkbox ---- */
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border-radius: 5px;
    border: 1px solid {p['BORDER']}; background: {p['INPUT_BG']};
}}
QCheckBox::indicator:checked {{
    background: {p['ACCENT']}; border-color: {p['ACCENT']}; image: none;
}}
QCheckBox::indicator:hover {{ border-color: {p['ACCENT']}; }}

/* ---- Table ---- */
QTableView {{
    background: {p['SURFACE']};
    alternate-background-color: {p['SURFACE_ALT']};
    border: 1px solid {p['BORDER']};
    border-radius: 10px;
    gridline-color: transparent;
    selection-background-color: rgba(99,102,241,0.25);
    selection-color: {p['TEXT']};
    outline: none;
}}
QTableView::item {{ padding: 6px 8px; border: none; }}
QHeaderView::section {{
    background: {p['BG']};
    color: {p['TEXT_MUTED']};
    padding: 9px 8px;
    border: none;
    border-bottom: 1px solid {p['BORDER']};
    font-weight: 600; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.5px;
}}
QTableCornerButton::section {{ background: {p['BG']}; border: none; }}

/* ---- Progress bar (dialogs / fallback) ---- */
QProgressBar {{
    background: {p['TRACK']}; border: none; border-radius: 6px;
    text-align: center; color: {p['TEXT']};
}}
QProgressBar::chunk {{ background: {p['ACCENT']}; border-radius: 6px; }}

/* ---- Scrollbars ---- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {p['BORDER']}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['ACCENT']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {p['BORDER']}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p['ACCENT']}; }}

/* ---- Tooltips ---- */
QToolTip {{
    background: {p['SURFACE']}; color: {p['TEXT']};
    border: 1px solid {p['BORDER']}; border-radius: 6px; padding: 6px 8px;
}}
"""


# ---- Preference persistence ---------------------------------------------
# The stored *preference* is tri-state: "system" (follow the OS, the default on
# first launch), or an explicit "dark"/"light" the user chose by hand. The
# *effective* mode (what's actually painted) is always "dark" or "light".

SYSTEM = "system"
_PREF_KEY = "ui/theme"


def preference() -> str:
    """Return the saved preference: ``"system"``, ``"dark"`` or ``"light"``."""
    try:
        from PySide6.QtCore import QSettings

        from core.config import QSETTINGS_APP, QSETTINGS_ORG
        value = QSettings(QSETTINGS_ORG, QSETTINGS_APP).value(_PREF_KEY)
        if value in _PALETTES or value == SYSTEM:
            return value
    except Exception:  # pragma: no cover - defensive; never block startup
        pass
    return SYSTEM


def set_preference(pref: str) -> None:
    """Persist the theme preference (``"system"``/``"dark"``/``"light"``)."""
    if pref not in _PALETTES and pref != SYSTEM:
        return
    try:
        from PySide6.QtCore import QSettings

        from core.config import QSETTINGS_APP, QSETTINGS_ORG
        QSettings(QSETTINGS_ORG, QSETTINGS_APP).setValue(_PREF_KEY, pref)
    except Exception:  # pragma: no cover - defensive
        pass


# ---- OS colour-scheme detection -----------------------------------------

def os_mode(app) -> str:
    """Detect the OS colour scheme (Qt 6.5+); default ``"dark"`` if unknown."""
    try:
        from PySide6.QtCore import Qt
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return "light"
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
    except Exception:  # pragma: no cover - older Qt / headless
        pass
    return _DEFAULT_MODE


def resolve(app, pref: str | None = None) -> str:
    """Resolve a preference to a concrete ``"dark"``/``"light"`` mode."""
    pref = pref or preference()
    if pref in _PALETTES:
        return pref
    return os_mode(app)


# ---- Public API ----------------------------------------------------------

def apply(app, mode: str) -> str:
    """Paint a concrete theme (``"dark"``/``"light"``) — no persistence.

    Sets the Fusion base style, installs the stylesheet, and updates the live
    palette read by hand-painting delegates. Returns the applied mode.
    """
    global _mode, _current
    if mode not in _PALETTES:
        mode = _DEFAULT_MODE
    _mode = mode
    _current = _PALETTES[mode]
    try:
        app.setStyle("Fusion")
    except Exception:  # pragma: no cover - style always present, be defensive
        pass
    app.setStyleSheet(_build_qss(_current))
    return mode


def init_theme(app) -> str:
    """Startup helper: apply the saved preference (OS-resolved) and return it."""
    return apply(app, resolve(app))
