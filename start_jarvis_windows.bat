@echo off
REM -- CAELUS . give the assistant a brain, on your own PC -------------------
REM
REM Double-click this file. It starts the model the assistant uses, so no key
REM and no internet are needed once the first download is done.
REM
REM WHY A SCRIPT AND NOT TWO LINES OF INSTRUCTIONS. Two of the three steps
REM fail silently on Windows. The page is opened as a FILE, so its origin is
REM the literal string "null", which is not on Ollama's default allowlist --
REM every call is refused before it is sent and nothing on screen says why.
REM OLLAMA_ORIGINS below is the whole fix. And the server answers on 11434
REM whether or not a model has been pulled, so a first question fails on an
REM empty model list rather than on anything that names itself.
REM
REM Works with Ollama installed normally (OllamaSetup.exe) or with ollama.exe
REM sitting in this same folder.

cd /d "%~dp0"
title CAELUS - local assistant
echo -- CAELUS . local assistant ------------------------------
echo.

REM 1. find ollama.exe: this folder first, then the PATH, then the per-user
REM    location the installer uses without asking
set "OLLAMA="
if exist "%~dp0ollama.exe" set "OLLAMA=%~dp0ollama.exe"
if not defined OLLAMA for %%I in (ollama.exe) do if not "%%~$PATH:I"=="" set "OLLAMA=%%~$PATH:I"
if not defined OLLAMA if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"

if not defined OLLAMA (
  echo Ollama is not installed yet.
  echo.
  echo    1. Go to  https://ollama.com/download/windows
  echo    2. Download OllamaSetup.exe and run it
  echo    3. Double-click THIS file again
  echo.
  echo No account, no key, no payment -- it is free and it runs on this PC.
  echo.
  pause
  exit /b 1
)

echo Found: %OLLAMA%
echo.

REM 2. stop any server that is already up. This is the failure that would
REM    otherwise look exactly like a bug: OllamaSetup.exe installs a tray app
REM    that starts its own server on 11434 WITHOUT the origins setting below.
REM    Our "serve" would then lose the port, every check would still pass --
REM    something is answering -- and the page would be refused anyway, with
REM    nothing naming the reason. So the old one goes and ours takes the port.
taskkill /f /im "ollama app.exe" >nul 2>&1
taskkill /f /im ollama.exe >nul 2>&1
ping -n 2 127.0.0.1 >nul

REM 3. start the server. OLLAMA_ORIGINS is what lets a page opened as a file
REM    read the answers; without it every call is refused with no explanation.
set "OLLAMA_ORIGINS=*"
set "OLLAMA_HOST=127.0.0.1:11434"
echo Starting the model server ...
start "CAELUS model server" /min "%OLLAMA%" serve

REM 4. wait until it actually answers before asking it for anything. "list"
REM    is the readiness check because it fails until the server is up, and it
REM    needs nothing installed on the PC to run -- older Windows has no curl.
set READY=
for /l %%i in (1,1,40) do (
  if not defined READY (
    "%OLLAMA%" list >nul 2>&1 && set READY=1
    if not defined READY ping -n 2 127.0.0.1 >nul
  )
)
if not defined READY (
  echo.
  echo The server did not start. Its own error is in the minimised window
  echo called "CAELUS model server" down in the taskbar.
  echo.
  pause
  exit /b 1
)

REM 5. pull the model. This is safe to run every time -- when the model is
REM    already here it checks and returns in a second, so there is no fragile
REM    "is it installed" parsing to get wrong.
echo Checking the model ^(first time: about 2 GB, a few minutes^) ...
echo.
"%OLLAMA%" pull llama3.2
if errorlevel 1 (
  echo.
  echo The download failed. Check the internet connection and run this again.
  pause
  exit /b 1
)

echo.
echo -- Ready -------------------------------------------------
echo Open celestial_alpha_b196.html and click Celestial.
echo The panel should turn green: "Running on your machine".
echo.
echo Leave THIS WINDOW OPEN while you use it.
echo Closing it stops the model.
echo ----------------------------------------------------------
echo.
pause
