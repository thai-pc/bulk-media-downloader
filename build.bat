@echo off
REM ===================================================================
REM  Build a single-file Windows executable with PyInstaller.
REM  Prerequisites:
REM    pip install -r requirements-dev.txt
REM    (optional) place ffmpeg.exe next to the produced .exe for
REM    video+audio merging and mp3 extraction.
REM
REM  Size note: we do NOT use --collect-submodules PySide6 (that pulls
REM  the entire Qt stack, incl. WebEngine, ~200MB+). PyInstaller's
REM  PySide6 hook already bundles the modules we import (QtWidgets/
REM  QtCore/QtGui) plus the required platform plugins. We also exclude
REM  the heaviest unused Qt modules explicitly to keep the .exe small.
REM ===================================================================

setlocal

REM Clean previous build artifacts.
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM Bundle ffmpeg/ffprobe into the .exe if present next to this script, so the
REM app can merge video+audio and make MP3 with no separate install. Download a
REM Windows build of ffmpeg and drop ffmpeg.exe (and ffprobe.exe) here first.
set "FFMPEG_ARGS="
if exist ffmpeg.exe set "FFMPEG_ARGS=%FFMPEG_ARGS% --add-binary ffmpeg.exe;."
if exist ffprobe.exe set "FFMPEG_ARGS=%FFMPEG_ARGS% --add-binary ffprobe.exe;."

pyinstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name BulkMediaDownloader ^
  --icon assets/icon.ico ^
  %FFMPEG_ARGS% ^
  --collect-all yt_dlp ^
  --collect-all gallery_dl ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtWebEngineQuick ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQuick3D ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtQuickWidgets ^
  --exclude-module PySide6.Qt3DCore ^
  --exclude-module PySide6.Qt3DRender ^
  --exclude-module PySide6.QtCharts ^
  --exclude-module PySide6.QtDataVisualization ^
  --exclude-module PySide6.QtMultimedia ^
  --exclude-module PySide6.QtMultimediaWidgets ^
  --exclude-module PySide6.QtPdf ^
  --exclude-module PySide6.QtPdfWidgets ^
  --exclude-module PySide6.QtDesigner ^
  --exclude-module PySide6.QtHelp ^
  --exclude-module PySide6.QtSql ^
  --exclude-module PySide6.QtTest ^
  --exclude-module PySide6.QtOpenGL ^
  --exclude-module PySide6.QtOpenGLWidgets ^
  --exclude-module tkinter ^
  app.py

echo.
echo Build complete. See dist\BulkMediaDownloader.exe
echo Remember to ship ffmpeg.exe alongside it for best results.

endlocal
