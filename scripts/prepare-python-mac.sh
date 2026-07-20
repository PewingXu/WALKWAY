#!/usr/bin/env bash
# =============================================================================
# 准备内置 macOS Python 运行时（自带报告依赖，可随 app 分发，对方 Mac 无需装 Python）
#
# 在【Mac】上运行：  bash scripts/prepare-python-mac.sh
# 结果：把一个独立的 CPython 3.13 + numpy/pandas/scipy/opencv/matplotlib/reportlab/pillow
#       安装到 python/runtime/，electron-builder 会把它打进 app（resources/python/runtime）。
#       electron/pythonRuntime.js 运行时自动找 resources/python/runtime/bin/python3.13。
#
# 用的是 astral-sh/python-build-standalone 的 install_only 版（可重定位、自包含）。
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
DEST="$ROOT/python/runtime"

# 1. 按 Mac 架构选安装包（Apple Silicon = aarch64；Intel = x86_64）
case "$(uname -m)" in
  arm64|aarch64) TRIPLE="aarch64-apple-darwin" ;;
  x86_64)        TRIPLE="x86_64-apple-darwin" ;;
  *) echo "不支持的架构: $(uname -m)"; exit 1 ;;
esac
echo "[1/4] 架构: $(uname -m) -> $TRIPLE"

# 2. 找最新 release 里匹配的 3.13 install_only 资源
API="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
URL=$(curl -fsSL "$API" | grep -oE "https://[^\"]*cpython-3\.13\.[0-9]+\+[0-9]+-${TRIPLE}-install_only\.tar\.gz" | head -1)
if [ -z "$URL" ]; then
  echo "未自动找到 3.13 ${TRIPLE} 的 install_only 包。"
  echo "请到 https://github.com/astral-sh/python-build-standalone/releases 手动挑一个"
  echo "  cpython-3.13.x+YYYYMMDD-${TRIPLE}-install_only.tar.gz，把 URL 赋给环境变量 PY_URL 重跑本脚本。"
  URL="${PY_URL:-}"
  [ -n "$URL" ] || exit 1
fi
echo "[2/4] 下载: $URL"

# 3. 下载解压到 python/runtime
TMP="$(mktemp -d)"
curl -fL "$URL" -o "$TMP/py.tar.gz"
tar -xzf "$TMP/py.tar.gz" -C "$TMP"          # 解出 $TMP/python
rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
mv "$TMP/python" "$DEST"
rm -rf "$TMP"
echo "[3/4] 已解压到 $DEST"

# 4. 装报告依赖
PY="$DEST/bin/python3.13"
[ -x "$PY" ] || PY="$DEST/bin/python3"
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r "$ROOT/python/requirements.txt"

echo "[4/4] 校验依赖..."
"$PY" -c "import numpy,pandas,scipy,matplotlib,cv2,reportlab,PIL; print('  ✓ 报告依赖齐全')"
echo "完成。内置 Python: $PY"
