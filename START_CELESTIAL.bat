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
REM  arrive over http.
REM
REM  This starts a small local server and opens the page on it. Nothing leaves
REM  the machine; 127.0.0.1 is this computer talking to itself.
REM
REM  THE FIRST VERSION OF THIS FILE OPENED THE BROWSER BEFORE THE SERVER WAS
REM  UP, and the browser knocked on a door that was not open yet - "localhost
REM  refused to connect". The server goes first now and the browser follows a
REM  few seconds later, from a second process, so the order is guaranteed
REM  rather than hoped for.
REM ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"
title Celestial Alpha

REM --- find a Python that actually runs.
REM     "where python" is not enough on Windows: a fresh machine has a stub at
REM     WindowsApps\python.exe that opens the Microsoft Store instead of
REM     running anything, and it answers "where" perfectly happily. The only
REM     honest test is to ask it its version and see if it replies.
set "PY="
for %%C in (py python python3) do (
  if not defined PY (
    %%C -V >nul 2>nul && set "PY=%%C"
  )
)

if not defined PY (
  echo.
  echo   Python is not installed - or the copy Windows found is the Microsoft
  echo   Store placeholder, which cannot run anything.
  echo.
  echo   Install it from  https://www.python.org/downloads/
  echo   TICK "Add Python to PATH" ON THE FIRST SCREEN. It is easy to miss,
  echo   and skipping it is why this will not find it afterwards.
  echo.
  echo   Until then the app still works. Open the html file directly - you
  echo   only lose the wake word. Type  celesx  in the chat instead and you
  echo   get exactly the same answers.
  echo.
  pause
  exit /b 1
)

echo   Using Python: %PY%

REM --- the bridge, if it is here and its dependencies are installed.
REM     It serves the page AND is what CELESX runs on, so it is one window
REM     rather than two.
if exist "celestial_bridge.py" (
  %PY% -c "import fastapi, uvicorn" >nul 2>nul
  if not errorlevel 1 (
    echo   Starting the bridge on http://127.0.0.1:8770
    echo   Leave this window open. Closing it stops the server.
    echo.
    start "" /b cmd /c "timeout /t 4 /nobreak >nul & explorer http://127.0.0.1:8770/app"
    %PY% celestial_bridge.py
    echo.
    echo   The server stopped. Anything printed above is the reason.
    pause
    goto :done
  )
  echo   celestial_bridge.py is here but FastAPI is not installed.
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

REM --- spaces and brackets are legal in a filename and illegal in a URL
set "URLNAME=%PAGE%"
set "URLNAME=!URLNAME: =%%20!"
set "URLNAME=!URLNAME:(=%%28!"
set "URLNAME=!URLNAME:)=%%29!"

echo   Serving  !PAGE!
echo   Opening  http://localhost:8099/!URLNAME!
echo   Leave this window open. Closing it stops the server.
echo.

REM --- browser second, and from its own process, so the server is listening
REM     by the time it knocks
REM     explorer, not start, because "start" reads its first quoted argument
REM     as a window title and the escaping needed to avoid that inside a
REM     nested cmd /c is the kind of thing that silently opens nothing.
REM     The url is percent-encoded already, so it needs no quotes at all.
start "" /b cmd /c "timeout /t 3 /nobreak >nul & explorer http://localhost:8099/!URLNAME!"

%PY% -m http.server 8099
echo.
echo   The server stopped. If that was immediate, the likely reason is that
echo   port 8099 is already taken by something else - close the other window
echo   and run this again.
pause

:done
endlocal
