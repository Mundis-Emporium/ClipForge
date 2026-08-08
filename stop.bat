@echo off
setlocal
REM Stop ClipForge (frees port 8765)
echo Stopping ClipForge...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765" ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>nul
echo ClipForge stopped.
timeout /t 2 >nul
