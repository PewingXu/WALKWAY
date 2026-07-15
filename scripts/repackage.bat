@echo off
setlocal
cd /d "%~dp0.."

REM ============================================================
REM  轻量重打包：仅当只改了代码（前端/electron/python 脚本），
REM  且 python\runtime 与 node_modules 已由 build-installer.bat 准备好时使用。
REM  只做 前端编译 + electron-builder 重打包，不重装 Python/依赖。
REM ============================================================

if not exist "python\runtime\python.exe" (
  echo [ERROR] python\runtime 不存在。请先运行 scripts\build-installer.bat 做完整构建。
  echo Press any key to close...
  pause >nul
  exit /b 1
)

echo [1/1] build frontend + electron-builder ...
call npm run build
if errorlevel 1 (
  echo [FAILED] 重打包失败，见上方日志。
  echo Press any key to close...
  pause >nul
  exit /b 1
)

echo.
echo ============================================================
echo [DONE] installer in dist\ :
dir /b dist\*.exe
echo ============================================================
echo Press any key to close...
pause >nul
endlocal
