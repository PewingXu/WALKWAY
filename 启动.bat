@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ===== 步道足底压力采集系统 启动脚本 =====
rem 直接双击本文件即可启动。

rem 清掉会导致 Electron 无法弹窗的环境变量
set ELECTRON_RUN_AS_NODE=

rem 加载已构建的前端 dist（稳定，不依赖 vite）
set WALKWAY_LOAD_DIST=1

rem 使用项目内置的 Python 环境（已装好报告依赖）
set WALKWAY_PYTHON=%~dp0python\venv\Scripts\python.exe

rem 演示模式：无真实设备时用合成数据看热力图。
rem 【接了真实步道设备后，把下面这一行删掉或在行首加 rem】
set WALKWAY_MOCK=1

npm start

pause
