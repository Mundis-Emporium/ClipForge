@echo off
setlocal
cd /d "%~dp0"
set "PYEXE=.venv\Scripts\python.exe"

REM stop ANY previous ClipForge (port holder AND stray python running app.py,
REM including one accidentally started with the wrong/system Python) so we
REM always run exactly one instance on the correct venv interpreter.
echo [ClipForge] stopping any previous instance...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_stop_stray.ps1" >nul 2>nul

if not exist "%PYEXE%" (
  echo [ClipForge] first run - creating virtual env...
  where py >nul 2>nul && (py -m venv .venv) || (python -m venv .venv)
  echo [ClipForge] installing dependencies, please wait...
  "%PYEXE%" -m pip install --upgrade pip
  "%PYEXE%" -m pip install -r requirements.txt
) else (
  echo [ClipForge] checking dependencies...
  "%PYEXE%" -m pip install -q -r requirements.txt
)

echo [ClipForge] starting (native window)...
REM desktop.py opens ClipForge in its own app window (pywebview) and falls back
REM to the default browser automatically if a native webview is unavailable.
"%PYEXE%" desktop.py
echo.
echo [ClipForge] stopped. You can close this window.
pause
