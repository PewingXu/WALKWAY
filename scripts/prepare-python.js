/**
 * 准备内置 Python 运行时。
 *
 * 打包（electron-builder）时，python/ 会被作为 extraResources 拷进安装包的
 * resources/python，运行时由 electron/pythonRuntime.js 定位 resources/python/runtime/python.exe。
 * 因此打包前需要先把一个「自带 numpy/pandas/scipy/matplotlib/opencv/reportlab/pillow 的
 * 独立 Python 运行时」放到 D:\walkway\python\runtime。
 *
 * 用法：
 *   node scripts/prepare-python.js <源runtime目录>
 * 或设置环境变量：
 *   WALKWAY_PY_SRC=<源runtime目录> node scripts/prepare-python.js
 *
 * <源runtime目录> 应包含 python.exe 及 Lib/site-packages（已装好依赖）。
 * 可用 requirements.txt（python/requirements.txt）先在一个 embeddable/venv Python 里
 * pip install，再把该 Python 目录作为源传入。
 */
const fs = require('fs')
const path = require('path')

const projectRoot = path.join(__dirname, '..')
const destDir = path.join(projectRoot, 'python', 'runtime')

const src = process.argv[2] || process.env.WALKWAY_PY_SRC
if (!src) {
  console.error('用法: node scripts/prepare-python.js <源runtime目录>')
  console.error('（源目录需含 python.exe 与已安装依赖的 Lib/site-packages）')
  process.exit(1)
}
if (!fs.existsSync(src)) {
  console.error('源目录不存在:', src)
  process.exit(1)
}

// 跳过无用/体积项
const SKIP_DIR_NAMES = new Set(['__pycache__', 'tests', 'test'])

function copyDir(from, to) {
  fs.mkdirSync(to, { recursive: true })
  for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
    const s = path.join(from, entry.name)
    const d = path.join(to, entry.name)
    if (entry.isDirectory()) {
      if (SKIP_DIR_NAMES.has(entry.name)) continue
      copyDir(s, d)
    } else if (entry.isFile()) {
      if (entry.name.endsWith('.pyc')) continue
      fs.copyFileSync(s, d)
    }
  }
}

console.log('准备内置 Python 运行时...')
console.log('  源:', src)
console.log('  目标:', destDir)
if (fs.existsSync(destDir)) {
  console.log('  目标已存在，先删除...')
  fs.rmSync(destDir, { recursive: true, force: true })
}
copyDir(src, destDir)

const py = path.join(destDir, 'python.exe')
if (!fs.existsSync(py)) {
  console.warn('[警告] 目标目录未发现 python.exe，请确认源目录是否正确:', py)
} else {
  console.log('[完成] 内置 Python 就绪:', py)
}
