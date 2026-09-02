@echo off
REM ===========================================================================
REM  Celestial Alpha - double-click this.
REM
REM  WHY THIS FILE EXISTS
REM
REM  Opening celestial_alpha.html by double-clicking it gives the browser a
REM  file:// address, and Chrome will not hand the microphone to a file://
REM  page. That is a browser rule, not a setting - so the wake word can never
REM  work that way, however many times it is switched on. The page has to
REM  arrive over http instead.
REM
REM  This starts a small local server and opens the page on it. Nothing leaves
REM  the machine; 127.0.0.1 is this computer talking to itself.
REM
REM  IT PREFERS THE BRIDGE. If celestial_bridge.py is here, that is started -
REM  it serves the page AND is the thing CELESX runs on, so it is one window
REM  rather than two. If it is not here, or FastAPI is not installed, it falls
REM  back to Python's own built-in server, which needs nothing installed.
REM ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"
title Celestial Alpha

REM --- find Python. "py" is the Windows launcher and the usual one; "python"
REM     is what python.org installs. Either will do.
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (where python3 >nul 2>nul && set "PY=python3")

if not defined PY (
  echo.
  echo   Python is not installed, and the page needs a local server before
  echo   the browser will allow the microphone.
  echo.
  echo   Install it from  https://www.python.org/downloads/
  echo   Tick "Add Python to PATH" on the first screen, then run this again.
  echo.
  echo   Until then the app still works - open the html file directly. You
  echo   just cannot use the wake word; type  celesx  in the chat instead,
  echo   which gives exactly the same answers.
  echo.
  pause
  exit /b 1
)

REM --- the bridge, if it is here and its dependencies are installed
if exist "celestial_bridge.py" (
  %PY% -c "import fastapi, uvicorn" >nul 2>nul
  if not errorlevel 1 (
    echo.
    echo   Starting the bridge on http://127.0.0.1:8770
    echo   The app opens in a moment. Leave this window open; close it to stop.
    echo.
    start "" "http://127.0.0.1:8770/app"
    %PY% celestial_bridge.py
    goto :done
  )
  echo.
  echo   celestial_bridge.py is here, but FastAPI is not installed.
  echo   For CELESX and syncing:   %PY% -m pip install fastapi uvicorn
  echo   Serving the page on its own for now.
  echo.
)

REM --- the fallback: Python's own server, which is always present.
REM     The newest celestial_alpha*.html wins, so a file still called
REM     "celestial_alpha (34).html" straight out of Downloads opens fine.
set "PAGE="
for /f "delims=" %%F in ('dir /b /o-d "celestial_alpha*.html" 2^>nul') do (
  if not defined PAGE set "PAGE=%%F"
)

if not defined PAGE (
  echo.
  echo   There is no celestial_alpha html file in this folder.
  echo   Put this .bat beside the page and run it again.
  echo.
  pause
  exit /b 1
)

REM --- spaces and brackets are legal in a filename and illegal in a URL, so
REM     they are encoded here rather than left to break the address
set "URLNAME=%PAGE%"
set "URLNAME=!URLNAME: =%%20!"
set "URLNAME=!URLNAME:(=%%28!"
set "URLNAME=!URLNAME:)=%%29!"

echo.
echo   Serving  !PAGE!
echo   Opening  http://localhost:8099/!URLNAME!
echo   Leave this window open; close it to stop.
echo.
start "" "http://localhost:8099/!URLNAME!"
%PY% -m http.server 8099

:done
endlocal
