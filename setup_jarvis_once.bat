@echo off
REM -- CAELUS . run this ONCE, then never think about it again ---------------
REM
REM WHAT THIS IS FOR. start_jarvis_windows.bat works, but it holds the model
REM server inside its own console window: close the window and the assistant
REM dies. That is a bad trade for something meant to be always there.
REM
REM Ollama's installer already puts a tray app in Startup, so a server is
REM running from the moment Windows logs in -- it just starts WITHOUT
REM OLLAMA_ORIGINS, and a page opened as a file has the origin "null", which
REM that server refuses. One setting is the whole difference.
REM
REM setx writes the setting into the user's environment permanently, so the
REM tray app picks it up at every login from now on. After this runs once:
REM no console window, nothing to start, nothing to remember. Close anything
REM you like -- the assistant is up because Windows brought it up.
REM
REM To undo: setx OLLAMA_ORIGINS "" (or delete it in
REM Settings > System > About > Advanced system settings > Environment Variables)

setlocal
cd /d "%~dp0"
title CAELUS - one-time setup
echo -- CAELUS . one-time setup -------------------------------
echo.

set "OLLAMA="
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not defined OLLAMA for %%I in (ollama.exe) do if not "%%~$PATH:I"=="" set "OLLAMA=%%~$PATH:I"

if not defined OLLAMA (
  echo Ollama is not installed. Install it first from
  echo    https://ollama.com/download/windows
  echo then run this file again.
  echo.
  pause
  exit /b 1
)

echo Found: %OLLAMA%
echo.

REM 1. the setting, written permanently into this user's environment
echo Saving the setting permanently ...
setx OLLAMA_ORIGINS "*" >nul
if errorlevel 1 (
  echo Could not save the setting. Try right-click this file and
  echo "Run as administrator".
  echo.
  pause
  exit /b 1
)
echo    OLLAMA_ORIGINS = *
echo.

REM 2. restart what is running, so it picks the setting up now instead of at
REM    the next login. setx only reaches processes started AFTER it, which is
REM    exactly why the running one has to go.
echo Restarting Ollama so it takes effect now ...
taskkill /f /im "ollama app.exe" >nul 2>&1
taskkill /f /im ollama.exe >nul 2>&1
ping -n 3 127.0.0.1 >nul

set "OLLAMA_ORIGINS=*"
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" (
  REM the tray app: no window, and Windows starts it again at every login
  start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
) else (
  REM no tray app installed -- fall back to a hidden server for this session
  start "CAELUS model server" /min "%OLLAMA%" serve
)

set READY=
for /l %%i in (1,1,40) do (
  if not defined READY (
    "%OLLAMA%" list >nul 2>&1 && set READY=1
    if not defined READY ping -n 2 127.0.0.1 >nul
  )
)

echo.
if not defined READY (
  echo Ollama did not come back up. Restart the PC once -- the setting is
  echo saved, so it will start correctly on its own after a restart.
) else (
  echo -- Done --------------------------------------------------
  echo.
  echo You can close this window. You can close every window.
  echo The assistant starts with Windows from now on.
  echo.
  echo Open celestial_alpha_b196.html, click Celestial, and the panel
  echo should be green: "Running on your machine".
)
echo ----------------------------------------------------------
echo.
pause
