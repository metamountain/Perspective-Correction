@echo off
rem ---------------------------------------------------------------------------
rem  Run the correction over a folder and collect everything needed to judge the
rem  result: a self-describing log, a machine-readable report, and the detection
rem  overlays.  Drop a photo folder onto this file, or run it and answer.
rem
rem  It finds ComfyUI's python by itself, because that is the one with torch and
rem  CUDA, and uses it when SAM is asked for.  Everything lands in one folder.
rem ---------------------------------------------------------------------------
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PHOTOS=%~1"
if "%PHOTOS%"=="" set /p PHOTOS=Folder of photographs: 
if not exist "%PHOTOS%" (
  echo Folder not found: %PHOTOS%
  pause & exit /b 1
)

rem --- pick an interpreter ----------------------------------------------------
set "PY="
for %%D in (C D E F) do (
  for /d %%P in ("%%D:\ComfyUI*") do (
    if exist "%%~fP\python_embeded\python.exe" if "!PY!"=="" set "PY=%%~fP\python_embeded\python.exe"
  )
)
if "%PY%"=="" set "PY=python"
echo Using interpreter: %PY%

rem --- where the results go ---------------------------------------------------
for /f "tokens=1-3 delims=/.- " %%a in ("%DATE%") do set "STAMP=%%c%%b%%a"
set "STAMP=%STAMP: =%"
set "TIMEPART=%TIME::=%"
set "TIMEPART=%TIMEPART:.=%"
set "TIMEPART=%TIMEPART: =0%"
set "OUT=%~dp0bpc-run-%STAMP%-%TIMEPART:~0,6%"
mkdir "%OUT%" 2>nul
mkdir "%OUT%\corrected" 2>nul
mkdir "%OUT%\debug" 2>nul
set "LOG=%OUT%\log.txt"

echo.
echo Results will be written to:
echo   %OUT%
echo.

rem --- optional SAM -----------------------------------------------------------
set "SAMARGS="
set "SAMMODEL="
for %%D in (C D E F) do (
  for /d %%P in ("%%D:\ComfyUI*") do (
    if exist "%%~fP\ComfyUI\models\sams\sam_vit_b_01ec64.pth" if "!SAMMODEL!"=="" ^
      set "SAMMODEL=%%~fP\ComfyUI\models\sams\sam_vit_b_01ec64.pth"
  )
)
if not "%SAMMODEL%"=="" (
  echo Found a SAM checkpoint:
  echo   %SAMMODEL%
  set /p USESAM=Use it? [y/N] 
  if /i "!USESAM!"=="y" set "SAMARGS=--mask sam --sam-model "!SAMMODEL!""
)

set /p FOCAL=35mm-equivalent focal length, blank to let it estimate: 
set "FOCALARG="
if not "%FOCAL%"=="" set "FOCALARG=--focal-35mm %FOCAL%"

rem --- environment report, always, even if the run then fails -----------------
echo === bpc run %DATE% %TIME% > "%LOG%"
echo === interpreter: %PY% >> "%LOG%"
echo === photos: %PHOTOS% >> "%LOG%"
echo. >> "%LOG%"
"%PY%" rectify.py --sam-info %SAMARGS:--mask sam=% >> "%LOG%" 2>&1
echo. >> "%LOG%"

rem --- the run ----------------------------------------------------------------
"%PY%" rectify.py "%PHOTOS%" ^
    -o "%OUT%\corrected" ^
    --debug-dir "%OUT%\debug" ^
    --json-report "%OUT%\report.json" ^
    --log-file "%LOG%" ^
    --diagnostics -v %FOCALARG% %SAMARGS%

echo.
echo ---------------------------------------------------------------------------
echo Done.  To share the result, send:
echo    %OUT%\log.txt        the decisions, with the environment that made them
echo    %OUT%\report.json    the same, machine readable
echo    %OUT%\debug\*.jpg    what the detector saw
echo ---------------------------------------------------------------------------
pause
