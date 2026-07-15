#!/usr/bin/env bash
# ============================================================
#  WALKWAY 一键打包（Git Bash 版，配合 Claude 的 ! 前缀使用）
#  产出：dist/ 下的 NSIS 安装包「步道采集系统 Setup x.y.z.exe」
#
#  用法（项目根目录下）：
#     bash scripts/build-installer.sh
#
#  前置：已装 Node、C:\Python313（完整 CPython 3.13）、有网络。
# ============================================================
set -o pipefail
cd "$(dirname "$0")/.." || exit 1

STAGE=/d/_walkway_pyruntime          # bash 用
STAGE_WIN='D:/_walkway_pyruntime'    # 传给 node 用

echo "========================================"
echo "[1/6] 复制基础 Python -> $STAGE"
echo "========================================"
rm -rf "$STAGE" || { echo "[错误] 无法清理暂存目录"; exit 1; }
cp -r /c/Python313 "$STAGE" || { echo "[错误] 复制 Python 失败"; exit 1; }
"$STAGE/python.exe" --version || { echo "[错误] 暂存 python 不可用"; exit 1; }

echo "========================================"
echo "[2/6] 安装 Python 依赖（numpy/pandas/scipy/matplotlib/opencv/reportlab/pillow）"
echo "========================================"
"$STAGE/python.exe" -m pip install --upgrade pip
"$STAGE/python.exe" -m pip install -r python/requirements.txt || { echo "[错误] pip 装依赖失败"; exit 1; }

echo "========================================"
echo "[3/6] 生成内嵌运行时 python/runtime"
echo "========================================"
node scripts/prepare-python.js "$STAGE_WIN" || { echo "[错误] prepare-python 失败"; exit 1; }

echo "========================================"
echo "[4/6] 安装 Node 依赖（含 @electron/rebuild）"
echo "========================================"
npm install || { echo "[错误] npm install 失败"; exit 1; }

echo "========================================"
echo "[5/6] 为 Electron 重建 serialport 原生模块"
echo "========================================"
npx electron-rebuild -f -w serialport || echo "[警告] electron-rebuild 失败：实时串口采集可能不可用；通常需装 VS Build Tools(C++)。可先继续打包。"

echo "========================================"
echo "[6/6] 构建前端 + electron-builder 打包"
echo "========================================"
npm run build || { echo "[错误] 打包失败"; exit 1; }

echo "========================================"
echo "[完成] 安装包已生成："
ls -la dist/*.exe 2>/dev/null || echo "（未找到 exe，检查上方日志）"
echo "========================================"
