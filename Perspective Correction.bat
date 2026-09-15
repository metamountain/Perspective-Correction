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
cd /d "%~dp0"
start "" pythonw rectify.py --gui
