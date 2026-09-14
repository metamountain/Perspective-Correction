@echo off
rem Double-click launcher for Windows.
cd /d "%~dp0"
python rectify.py --gui
if errorlevel 1 pause
