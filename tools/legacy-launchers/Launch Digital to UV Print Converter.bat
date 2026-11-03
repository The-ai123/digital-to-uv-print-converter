@echo off
cd /d "%~dp0..\.."
if not exist ".venv\Scripts\pythonw.exe" (
  echo Install dependencies first. See README.md.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "uvconvert.py" gui
