@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0.."

REM ============================================================
REM  WALKWAY one-click installer build (includes current code changes)
REM  All ASCII. Output: dist\  NSIS installer exe
REM
REM  - If embedded python runtime AND node_modules are ready ->
REM      FAST repackage (frontend build + electron-builder only).
REM  - Otherwise -> FULL build (scripts\build-installer.bat:
REM      python runtime + deps + native rebuild + package).
REM
REM  Current working-tree changes (front-end / electron / serial) are
REM  compiled and bundled straight from disk, so NO git commit is
REM  required for them to be included in the installer.
REM ============================================================

echo.
echo [check] embedded python runtime + node_modules ...
set "NEED_FULL="
if not exist "python\runtime\python.exe" set "NEED_FULL=1"
if not exist "node_modules\electron" set "NEED_FULL=1"

if defined NEED_FULL (
  echo   -^> prerequisites missing, running FULL build via build-installer.bat
  call "scripts\build-installer.bat"
  exit /b %errorlevel%
)

echo   -^> prerequisites present, running FAST repackage
echo.
echo [1/1] build frontend + electron-builder ...
call npm run build
if errorlevel 1 ( echo [FAILED] build failed, see log above. & goto :fail )

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
