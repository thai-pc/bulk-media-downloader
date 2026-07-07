@echo off
REM ===================================================================
REM  Build a single-file Windows executable with PyInstaller.
REM  Prerequisites:
REM    pip install -r requirements-dev.txt
REM    (optional) place ffmpeg.exe next to the produced .exe for
REM    video+audio merging and mp3 extraction.
REM ===================================================================

setlocal

REM Clean previous build artifacts.
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

pyinstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name BulkMediaDownloader ^
  --collect-all yt_dlp ^
  --collect-all gallery_dl ^
  --collect-submodules PySide6 ^
  app.py

echo.
echo Build complete. See dist\BulkMediaDownloader.exe
echo Remember to ship ffmpeg.exe alongside it for best results.

endlocal
