@echo off
rem Double-click launcher for Windows.
rem
rem pythonw, not python: python.exe is a console program, so cmd held a black
rem window open behind the application for its whole life.  `start ""` then lets
rem this script exit immediately instead of waiting, so the console that ran the
rem script closes too and nothing is left on screen.
rem
rem The cost of pythonw is that there is no stdout to read a crash from. That is
rem already handled: the application writes failures to pc_errors.log, which is
rem where to look when it does not come up. Do not "fix" this by going back to
rem python.exe -- check that file instead.
rem
rem Three tiers, first one that exists wins:
rem   1. .pc_python  -- one line naming the pythonw.exe of an external venv that
rem                     `tools/install_deps.py --venv <dir>` pinned for this copy
rem   2. .venv\Scripts\pythonw.exe  -- a venv created beside the code
rem   3. pythonw on the PATH
cd /d "%~dp0"
set "PC_PYTHON="
if exist ".pc_python" set /p PC_PYTHON=<.pc_python
if defined PC_PYTHON if exist "%PC_PYTHON%" (
    start "" "%PC_PYTHON%" perspective_correction.py --gui
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" perspective_correction.py --gui
) else (
    start "" pythonw perspective_correction.py --gui
)
