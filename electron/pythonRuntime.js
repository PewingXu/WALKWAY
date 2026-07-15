const fs = require('fs')
const os = require('os')
const path = require('path')

const PY_VERSION = '3.13'

function getResourcesBase(resourceBase = null) {
  return resourceBase || process.env.resourcesPath || process.resourcesPath || ''
}

function getPackagedRuntimeDir(resourceBase = process.resourcesPath) {
  const base = getResourcesBase(resourceBase)
  if (!base) return null

  const runtimeDir = path.join(base, 'python', 'runtime')
  return fs.existsSync(runtimeDir) ? runtimeDir : null
}

function getPackagedPythonBinary(resourceBase = process.resourcesPath) {
  const runtimeDir = getPackagedRuntimeDir(resourceBase)
  if (!runtimeDir) return null

  const candidates = process.platform === 'win32'
    ? [path.join(runtimeDir, 'python.exe')]
    : [
        path.join(runtimeDir, 'bin', `python${PY_VERSION}`),
        path.join(runtimeDir, 'bin', 'python3'),
        path.join(runtimeDir, 'bin', 'python'),
      ]

  return candidates.find((candidate) => fs.existsSync(candidate)) || null
}

function getPackagedSitePackages(resourceBase = process.resourcesPath) {
  const base = getResourcesBase(resourceBase)
  if (!base) return null

  const runtimeSitePackages = path.join(base, 'python', 'runtime', 'Lib', 'site-packages')
  if (fs.existsSync(runtimeSitePackages)) return runtimeSitePackages

  const venvSitePackages = path.join(base, 'python', 'venv', 'lib', `python${PY_VERSION}`, 'site-packages')
  return fs.existsSync(venvSitePackages) ? venvSitePackages : null
}

function getPackagedMplConfigDir() {
  const dir = path.join(os.tmpdir(), 'walkway-mplconfig')
  fs.mkdirSync(dir, { recursive: true })
  return dir
}

function prependPathList(existingValue, entry) {
  if (!entry) return existingValue
  if (!existingValue) return entry

  const parts = existingValue.split(path.delimiter).filter(Boolean)
  if (parts.includes(entry)) return existingValue
  return [entry, ...parts].join(path.delimiter)
}

/**
 * 组装运行 Python 报告脚本的环境变量。
 * 打包后优先使用内置 runtime（resources/python/runtime）；
 * 开发期若无内置 runtime，则回退到系统 PATH 上的 python。
 */
function getPackagedPythonEnv({ baseEnv = process.env, resourceBase = process.resourcesPath } = {}) {
  const env = { ...baseEnv }
  const runtimeDir = getPackagedRuntimeDir(resourceBase)
  const sitePackages = getPackagedSitePackages(resourceBase)

  if (runtimeDir) {
    env.PYTHONHOME = runtimeDir
    env.PATH = prependPathList(env.PATH, runtimeDir)
  }
  if (sitePackages) {
    env.PYTHONPATH = prependPathList(env.PYTHONPATH, sitePackages)
  }

  env.MPLCONFIGDIR = getPackagedMplConfigDir()
  env.MPLBACKEND = 'Agg' // 无头环境强制非交互式后端
  env.PYTHONNOUSERSITE = '1'
  env.PYTHONDONTWRITEBYTECODE = '1'
  env.PYTHONUTF8 = '1'
  env.PYTHONIOENCODING = env.PYTHONIOENCODING || 'utf-8'
  return env
}

module.exports = {
  PY_VERSION,
  getPackagedRuntimeDir,
  getPackagedPythonBinary,
  getPackagedSitePackages,
  getPackagedPythonEnv,
}
