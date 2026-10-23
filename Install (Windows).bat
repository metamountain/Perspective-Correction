@echo off
rem Double-click installer for Windows. The work is in tools/install_deps.py;
rem this only finds a Python and keeps the window open. python on PATH first,
rem the py launcher second -- the same order "Perspective Correction.exe" uses,
rem so the packages land in the Python that starts the program.
rem Pass --lama for the lama fill.
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python tools\install_deps.py %*
) else (
    py -3 tools\install_deps.py %*
)
echo.
pause
