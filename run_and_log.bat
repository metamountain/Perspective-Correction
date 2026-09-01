@echo off
rem ---------------------------------------------------------------------------
rem  Run the correction over a folder and collect everything needed to judge the
rem  result: a self-describing log, a machine-readable report, and the detection
rem  overlays.  Drop a photo folder onto this file, or run it and answer.
rem
rem  Deliberately thin.  An earlier version searched for the model checkpoint here
rem  and grew a line continuation inside a parenthesised block, which cmd runs as
rem  a command -- so anything worth getting right is done by --birefnet-model auto
rem  in Python, where it is tested.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "PHOTOS=%~1"
if "%PHOTOS%"=="" set /p PHOTOS=Folder of photographs: 
if not exist "%PHOTOS%" (
  echo Folder not found: %PHOTOS%
  pause
  exit /b 1
)

rem --- pick an interpreter: ComfyUI's has torch and CUDA ----------------------
set "PY=python"
call :find_python C
call :find_python D
call :find_python E
call :find_python F
echo Using interpreter: %PY%

rem --- where the results go ---------------------------------------------------
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%t"
if "%STAMP%"=="" set "STAMP=run"
set "OUT=%~dp0bpc-run-%STAMP%"
mkdir "%OUT%" 2>nul
mkdir "%OUT%\corrected" 2>nul
mkdir "%OUT%\debug" 2>nul
set "LOG=%OUT%\log.txt"

echo.
echo Results will be written to:
echo   %OUT%
echo.

set "MASKARGS="
set /p USEMASK=Segment the building out and ignore the rest (BiRefNet)? [y/N] 
if /i "%USEMASK%"=="y" set "MASKARGS=--mask birefnet --birefnet-model auto"

set "FOCALARG="
set /p FOCAL=35mm-equivalent focal length, blank to let it estimate: 
if not "%FOCAL%"=="" set "FOCALARG=--focal-35mm %FOCAL%"

rem --- environment first, so even a failed run leaves something diagnosable ---
echo === bpc run %DATE% %TIME% > "%LOG%"
echo === interpreter: %PY% >> "%LOG%"
echo === photos: %PHOTOS% >> "%LOG%"
echo. >> "%LOG%"
"%PY%" rectify.py --mask-info --birefnet-model auto >> "%LOG%" 2>&1
echo. >> "%LOG%"

rem --- the run ----------------------------------------------------------------
"%PY%" rectify.py "%PHOTOS%" -o "%OUT%\corrected" --debug-dir "%OUT%\debug" --json-report "%OUT%\report.json" --log-file "%LOG%" --diagnostics -v %FOCALARG% %MASKARGS%

echo.
echo ---------------------------------------------------------------------------
echo Done.  To have the result reviewed, send:
echo    %OUT%\log.txt        the decisions, with the environment that made them
echo    %OUT%\report.json    the same, machine readable
echo    %OUT%\debug\*.jpg    what the detector saw
echo ---------------------------------------------------------------------------
pause
exit /b 0

:find_python
if not "%PY%"=="python" exit /b 0
for /d %%P in ("%~1:\ComfyUI*") do (
  if exist "%%~fP\python_embeded\python.exe" set "PY=%%~fP\python_embeded\python.exe"
)
exit /b 0
