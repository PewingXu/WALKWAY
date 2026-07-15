@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0.."

REM ============================================================
REM  WALKWAY one-click packaging (Windows). All ASCII.
REM  Output: dist\  NSIS installer exe
REM ============================================================

set "PY_SRC=C:\Python313"
set "STAGE=D:\_walkway_pyruntime"

echo.
echo [1/7] Copy base Python to %STAGE%
if exist "%STAGE%" rmdir /s /q "%STAGE%"
xcopy /e /i /q /y "%PY_SRC%" "%STAGE%" >nul
if errorlevel 1 ( echo [ERROR] copy python failed & goto :fail )

echo.
echo [2/7] pip install deps INTO staging (force, ignore user-site)
REM disable user-site so deps are really installed into staging\Lib\site-packages
set "PYTHONNOUSERSITE=1"
"%STAGE%\python.exe" -m pip install --upgrade pip
"%STAGE%\python.exe" -m pip install --ignore-installed --no-user --no-warn-script-location -r "python\requirements.txt"
if errorlevel 1 ( echo [ERROR] pip install failed & goto :fail )
set "PYTHONNOUSERSITE="

echo.
echo [3/7] build embedded runtime python\runtime
node scripts\prepare-python.js "%STAGE%"
if errorlevel 1 ( echo [ERROR] prepare-python failed & goto :fail )

echo.
echo [4/7] verify embedded runtime can import all deps (user-site disabled)
setlocal
set "PYTHONNOUSERSITE=1"
set "PYTHONHOME=%CD%\python\runtime"
"python\runtime\python.exe" -c "import numpy,scipy,pandas,matplotlib,cv2,reportlab,PIL;print('deps OK')"
if errorlevel 1 ( endlocal & echo [ERROR] embedded runtime is missing deps & goto :fail )
endlocal

echo.
echo [5/7] npm install (includes @electron/rebuild)
call npm install
if errorlevel 1 ( echo [ERROR] npm install failed & goto :fail )

echo.
echo [6/7] rebuild serialport native module for Electron
call npx electron-rebuild -f -w serialport
if errorlevel 1 ( echo [WARN] electron-rebuild failed - serial capture may not work; usually needs VS Build Tools C++. Continuing. )

echo.
echo [7/7] build frontend + electron-builder
call npm run build
if errorlevel 1 ( echo [ERROR] electron-builder failed & goto :fail )

echo.
echo ============================================================
echo [DONE] installer in dist\ :
dir /b dist\*.exe
echo ============================================================
echo Press any key to close...
pause >nul
endlocal
exit /b 0

:fail
echo.
echo ============================================================
echo [FAILED] See the log above. Copy the whole output to Claude.
echo ============================================================
echo Press any key to close...
pause >nul
endlocal
exit /b 1
