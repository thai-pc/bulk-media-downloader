"""Core package for Bulk Media Downloader.

This package is intentionally free of any PySide6/Qt imports so it can be used
headless (CLI mode) and in unit tests without a running ``QApplication``.
The only exception is :mod:`core.config`, which *lazily* imports ``QSettings``
inside two methods (never at module import time) so persistence works in the
GUI while the module stays importable without Qt.
"""

__version__ = "1.0.0"
