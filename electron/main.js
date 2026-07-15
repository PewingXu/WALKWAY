const { app, BrowserWindow, ipcMain, dialog, shell, protocol } = require('electron')
const path = require('path')
const fs = require('fs')
const http = require('http')
const { fork, spawn } = require('child_process')
const { getPackagedPythonBinary, getPackagedPythonEnv } = require('./pythonRuntime')

const isPackaged = app.isPackaged

// ── 前端资源位置 ──
const devServerUrl = process.env.VITE_DEV_SERVER_URL || 'http://localhost:5273'
const distDir = path.join(__dirname, '..', 'front-end', 'dist')

// 探测 Vite 开发服务器是否可达（开发期用）
function checkDevServer(url, timeoutMs = 800) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => { res.resume(); resolve(true) })
    req.on('error', () => resolve(false))
    req.setTimeout(timeoutMs, () => { req.destroy(); resolve(false) })
  })
}

// 轮询等待 Vite 就绪：concurrently 会并行启动 Vite 与 Electron，
// Electron 常抢跑（Vite 还没起来），单次探测失败就会误回退 dist。
// 这里重试等待，直到就绪或超时（默认 ~20s）。
async function waitForDevServer(url, { retries = 40, intervalMs = 500 } = {}) {
  for (let i = 0; i < retries; i++) {
    if (await checkDevServer(url)) return true
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  return false
}

// 自定义 app:// 协议加载构建产物。
// 原因：Vite 产出的 index.html 是 <script type="module" crossorigin>，
// ES module 恒以 CORS 模式加载；用 file:// 打开时源为 null 会被 CORS 拦截 → 白屏。
// 注册一个 standard+secure 的 app:// 协议来提供 dist 文件即可规避。
protocol.registerSchemesAsPrivileged([
  { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } },
])

const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.json': 'application/json', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
  '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff': 'font/woff',
  '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.map': 'application/json',
}

function registerAppProtocol() {
  protocol.handle('app', async (request) => {
    let pathname = '/'
    try { pathname = decodeURIComponent(new URL(request.url).pathname) } catch (e) {}
    if (!pathname || pathname === '/') pathname = '/index.html'
    // 防目录穿越：解析后必须仍在 distDir 内
    const filePath = path.normalize(path.join(distDir, pathname))
    if (!filePath.startsWith(distDir)) return new Response('forbidden', { status: 403 })
    try {
      const data = await fs.promises.readFile(filePath) // fs 支持读取 asar
      const ext = path.extname(filePath).toLowerCase()
      return new Response(data, { headers: { 'content-type': MIME[ext] || 'application/octet-stream' } })
    } catch (e) {
      return new Response('not found', { status: 404 })
    }
  })
}

// ── Python 报告脚本位置 ──
// 打包后 python 目录在 resources/python；开发期在项目根 python/
const pythonDir = isPackaged
  ? path.join(process.resourcesPath, 'python')
  : path.join(__dirname, '..', 'python')
const runReportScript = path.join(pythonDir, 'run_report.py')

// 开发期：自动定位项目内置 venv 的 python（scripts/prepare-python 未跑时的兜底）
// 这样无论用哪种方式启动（npm start / npm run dev / 启动.bat）都能用上装好依赖的解释器
function getDevVenvPython() {
  if (isPackaged) return null
  const candidates = process.platform === 'win32'
    ? [path.join(pythonDir, 'venv', 'Scripts', 'python.exe')]
    : [path.join(pythonDir, 'venv', 'bin', 'python3'), path.join(pythonDir, 'venv', 'bin', 'python')]
  return candidates.find((p) => fs.existsSync(p)) || null
}

// ── 设备后端（串口服务）位置 ──
const serialServerScript = path.join(__dirname, '..', 'serial', 'gaitSerialServer.js')

let mainWindow = null
let deviceChild = null
let deviceState = { ready: false, port: 19999, error: null }

/* ───────────────────────── 窗口 ───────────────────────── */
async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    backgroundColor: '#0A1628',
    show: false,
    icon: path.join(__dirname, '..', 'assets', 'logo.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  mainWindow.once('ready-to-show', () => mainWindow.show())

  mainWindow.webContents.on('did-finish-load', async () => {
    console.log('[window] page loaded')
    try {
      const info = await mainWindow.webContents.executeJavaScript(
        'JSON.stringify({ url: location.href, rootLen: (document.getElementById("root")||{}).innerHTML?.length||0 })'
      )
      console.log('[window] dom', info)
    } catch (e) { console.error('[window] probe err', e && e.message) }
  })
  mainWindow.webContents.on('did-fail-load', (_e, code, desc, url) =>
    console.error('[window] load failed', code, desc, url))
  mainWindow.webContents.on('console-message', (_e, level, message, line, sourceId) =>
    console.log(`[renderer:${level}] ${message} (${sourceId}:${line})`))
  mainWindow.webContents.on('render-process-gone', (_e, details) =>
    console.error('[renderer] gone', JSON.stringify(details)))
  mainWindow.webContents.on('preload-error', (_e, p, err) =>
    console.error('[preload] error', p, err && err.message))

  // 加载策略（自动判断，用户直接 npm start 即可）：
  //  - 打包后：加载内置 dist（app:// 协议）
  //  - 开发期：能连上 Vite 开发服务器就用它（热更新）；连不上就自动加载已构建的 dist
  //  - WALKWAY_LOAD_DIST=1 可强制用 dist（跳过探测）
  let useDevServer = false
  if (!isPackaged && process.env.WALKWAY_LOAD_DIST !== '1') {
    // npm run dev 会显式设置 VITE_DEV_SERVER_URL：轮询等待 Vite 就绪（避免抢跑回退 dist）。
    // 直接 electron . / npm start 未设置该变量：单次探测，连不上就用 dist。
    if (process.env.VITE_DEV_SERVER_URL) {
      console.log('[window] 等待 Vite 开发服务器就绪…', devServerUrl)
      useDevServer = await waitForDevServer(devServerUrl)
      if (!useDevServer) console.warn('[window] 等待 Vite 超时，回退加载 dist')
    } else {
      useDevServer = await checkDevServer(devServerUrl)
    }
  }

  if (useDevServer) {
    mainWindow.loadURL(devServerUrl)
    if (process.env.OPEN_DEVTOOLS !== '0') {
      mainWindow.webContents.openDevTools({ mode: 'detach' })
    }
  } else {
    if (!fs.existsSync(path.join(distDir, 'index.html'))) {
      console.error('[window] 未找到已构建的前端，请先运行 npm run build:front')
    }
    mainWindow.loadURL('app://local/index.html')
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

/* ─────────────────── 设备后端子进程 ─────────────────── */
function sendDeviceEvent(payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('device-event', payload)
  }
}

function startDeviceServer() {
  if (deviceChild) return
  if (!fs.existsSync(serialServerScript)) {
    deviceState = { ready: false, port: 19999, error: 'serial server not found' }
    console.warn('[device] serial server script missing:', serialServerScript)
    return
  }

  const child = fork(serialServerScript, [], {
    env: {
      ...process.env,
      isPackaged: String(isPackaged),
      appPath: app.getAppPath(),
      userData: app.getPath('userData'),
      resourcesPath: process.resourcesPath || '',
      // 登录页密钥写入的设备映射文件；顶到最高优先级（文件不存在时服务端自动回落到 serial/serial.txt）
      WALKWAY_SERIAL_TXT: getDeviceKeyPath(),
    },
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
  })
  deviceChild = child

  child.stdout && child.stdout.on('data', (d) => { const t = d.toString().trim(); if (t) sendLog(t, 'device') })
  child.stderr && child.stderr.on('data', (d) => { const t = d.toString().trim(); if (t) sendLog(t, 'device-err') })

  child.on('message', (msg) => {
    if (!msg || typeof msg !== 'object') return
    if (msg.type === 'ready') {
      deviceState = { ready: true, port: msg.port || 19999, error: null }
      sendLog(`设备服务就绪 ws://localhost:${deviceState.port}`, 'success')
    } else if (msg.type === 'error') {
      deviceState = { ...deviceState, error: msg.message || 'device error' }
      sendLog(`设备错误：${msg.message || ''}`, 'error')
    }
    sendDeviceEvent(msg)
  })

  child.on('exit', (code) => {
    console.log('[device] exited with code', code)
    deviceChild = null
    deviceState = { ready: false, port: deviceState.port, error: `exited(${code})` }
    sendDeviceEvent({ type: 'exit', code })
  })
}

function stopDeviceServer() {
  if (!deviceChild) return
  try {
    deviceChild.send({ type: 'shutdown' })
  } catch (e) {}
  try {
    deviceChild.kill('SIGTERM')
  } catch (e) {}
  deviceChild = null
}

// 重启串口服务：写入新设备映射后调用，让已连接的垫子按新映射重新识别。
// 等旧进程退出（释放 WS 端口）后再拉起新进程，避免端口占用冲突。
function restartDeviceServer() {
  const child = deviceChild
  if (!child) {
    startDeviceServer()
    return
  }
  deviceChild = null
  child.once('exit', () => setTimeout(startDeviceServer, 300))
  try {
    child.send({ type: 'shutdown' })
  } catch (e) {}
  try {
    child.kill('SIGTERM')
  } catch (e) {}
}

// 设备映射文件路径（userData/serial.txt）——登录页密钥写入的目标，也是串口服务读取的首选。
function getDeviceKeyPath() {
  return path.join(app.getPath('userData'), 'serial.txt')
}

// 从密钥的 key 字段提取有效的 MAC→footN 映射（与 gaitSerialServer 读取口径一致，用于校验/回显）。
function extractFootMap(keyField) {
  let mapObj = null
  if (keyField && typeof keyField === 'object' && !Array.isArray(keyField)) {
    mapObj = keyField
  } else if (typeof keyField === 'string') {
    let text = keyField.trim()
    // 云端下发格式 {"key":<map>,"orgName":...}：优先按整体 JSON 解析后取 .key
    try {
      const o = JSON.parse(text)
      if (o && typeof o === 'object') mapObj = o.key && typeof o.key === 'object' ? o.key : o
    } catch (e) {}
    if (!mapObj) {
      // 宽松字符串：形如 "MAC1":"foot1","MAC2":"foot2"
      const map = {}
      text
        .replace(/'/g, '"')
        .split(/[,;\n]+/)
        .forEach((part) => {
          const m = part.match(/^\s*"?([^":=]+)"?\s*[:=]\s*"?([^"]+)"?\s*$/)
          if (m) map[m[1].trim()] = m[2].trim()
        })
      if (Object.keys(map).length) mapObj = map
    }
  }
  const mapping = {}
  if (mapObj && typeof mapObj === 'object') {
    for (const k of Object.keys(mapObj)) {
      const v = String(mapObj[k]).trim().toLowerCase()
      if (/^foot[1-4]$/.test(v)) mapping[k] = v
    }
  }
  return { count: Object.keys(mapping).length, mapping }
}

/* ─────────────────────── IPC ─────────────────────── */
function registerIpc() {
  ipcMain.handle('get-app-version', () => ({ version: app.getVersion() }))

  ipcMain.handle('get-device-status', () => deviceState)

  // 登录页密钥 → 写入本地设备映射文件（serial.txt），并重启串口服务使映射立即生效。
  // 密钥即配置串：可为「纯映射对象」「云端下发格式 {key,orgName}」或「宽松字符串」。
  // 统一归一化为 { key:<映射>, orgName } 写入，与 gaitSerialServer 的读取口径一致。
  ipcMain.handle('save-device-key', async (_event, payload = {}) => {
    const key = typeof payload.key === 'string' ? payload.key.trim() : ''
    if (!key) return { ok: false, error: '密钥为空' }

    let fileObj
    try {
      const parsed = JSON.parse(key)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed) && parsed.key != null) {
        fileObj = parsed // 已是完整格式（含 key 字段），原样保留
      } else {
        fileObj = { key: parsed, orgName: 'walkway-local' }
      }
    } catch (e) {
      fileObj = { key, orgName: 'walkway-local' } // 宽松字符串，交给服务端宽松解析
    }

    const { count, mapping } = extractFootMap(fileObj.key)
    const content = JSON.stringify(fileObj, null, 2)
    const target = getDeviceKeyPath()

    let changed = true
    try {
      changed = fs.readFileSync(target, 'utf-8') !== content
    } catch (e) {}

    try {
      await fs.promises.mkdir(path.dirname(target), { recursive: true })
      await fs.promises.writeFile(target, content, 'utf-8')
    } catch (e) {
      return { ok: false, error: `写入设备映射失败：${e.message}` }
    }

    sendLog(`已写入设备映射（识别到 ${count} 块）：${target}`, count > 0 ? 'success' : 'info')
    if (changed) restartDeviceServer()
    return { ok: true, count, mapping, path: target }
  })

  ipcMain.handle('select-export-directory', async () => {
    const win = BrowserWindow.getFocusedWindow()
    const result = await dialog.showOpenDialog(win || undefined, {
      title: '选择导出文件夹',
      properties: ['openDirectory', 'createDirectory'],
    })
    return { canceled: result.canceled, path: result.filePaths?.[0] || '' }
  })

  ipcMain.handle('write-export-file', async (_event, payload = {}) => {
    const directoryPath = typeof payload.directoryPath === 'string' ? payload.directoryPath : ''
    const fileName = typeof payload.fileName === 'string' ? payload.fileName : ''
    if (!directoryPath || !fileName) throw new Error('missing export path')

    const safeFileName = path.basename(fileName)
    if (!safeFileName || safeFileName !== fileName) throw new Error('invalid export file name')

    const directory = path.resolve(directoryPath)
    await fs.promises.mkdir(directory, { recursive: true })
    const targetPath = path.resolve(directory, safeFileName)
    const prefix = directory.endsWith(path.sep) ? directory : `${directory}${path.sep}`
    if (!targetPath.startsWith(prefix)) throw new Error('invalid export target path')

    const data = payload.data
    const encoding = payload.encoding === 'base64' ? 'base64' : 'utf8'
    let buffer
    if (Buffer.isBuffer(data)) buffer = data
    else if (typeof data === 'string') buffer = Buffer.from(data, encoding)
    else throw new Error('invalid export file data')

    await fs.promises.writeFile(targetPath, buffer)
    return { path: targetPath }
  })

  // 生成报告（实时采集）：把 4 份 CSV 写入临时目录 → 跑 python → 打开 PDF
  ipcMain.handle('generate-report', async (_event, payload = {}) => {
    const csv = payload.csv || {}
    for (const k of ['1', '2', '3', '4']) {
      if (typeof csv[k] !== 'string' || !csv[k]) {
        return { ok: false, error: `缺少第 ${k} 块压力垫数据` }
      }
    }
    const workDir = makeWorkDir()
    for (const k of ['1', '2', '3', '4']) {
      await fs.promises.writeFile(path.join(workDir, `${k}.csv`), csv[k], 'utf8')
    }
    return runReport(workDir, payload.name, payload.weight)
  })

  // 选择包含 1~4.csv 的目录（导入 CSV 生成报告用）
  ipcMain.handle('select-import-directory', async () => {
    const win = BrowserWindow.getFocusedWindow()
    const result = await dialog.showOpenDialog(win || undefined, {
      title: '选择包含 1.csv~4.csv 的文件夹',
      properties: ['openDirectory'],
    })
    return { canceled: result.canceled, path: result.filePaths?.[0] || '' }
  })

  // 生成报告（导入 CSV）：从所选目录读取 1~4.csv → 拷入临时目录 → 跑 python → 打开 PDF
  ipcMain.handle('generate-report-from-dir', async (_event, payload = {}) => {
    const dir = typeof payload.dir === 'string' ? payload.dir : ''
    if (!dir) return { ok: false, error: '未选择目录' }
    for (const k of ['1', '2', '3', '4']) {
      if (!fs.existsSync(path.join(dir, `${k}.csv`))) {
        return { ok: false, error: `所选目录缺少 ${k}.csv` }
      }
    }
    // 拷到临时目录，避免污染源目录（脚本会生成 temp_denoised/ 与 pdf）
    const workDir = makeWorkDir()
    for (const k of ['1', '2', '3', '4']) {
      await fs.promises.copyFile(path.join(dir, `${k}.csv`), path.join(workDir, `${k}.csv`))
    }
    return runReport(workDir, payload.name, payload.weight)
  })
}

// 向渲染进程推送一条日志（终端式日志面板消费）
function sendLog(line, level = 'info') {
  const msg = { ts: Date.now(), level, line: String(line) }
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('app-log', msg)
  }
  console.log(`[log:${level}]`, line)
}

// 建一个时间戳命名的临时工作目录（放中间 CSV / 图表）
function makeWorkDir() {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-')
  const workDir = path.join(app.getPath('temp'), `walkway-${stamp}`)
  fs.mkdirSync(workDir, { recursive: true })
  return workDir
}

// 报告输出目录：开发期放项目根 reports/；打包后放「我的文档/步道报告」（安装目录通常不可写）
function getReportsDir() {
  const dir = isPackaged
    ? path.join(app.getPath('documents'), '步道报告')
    : path.join(__dirname, '..', 'reports')
  fs.mkdirSync(dir, { recursive: true })
  return dir
}

// 净化 Windows 文件名（去掉 \ / : * ? " < > | 及控制字符，限长）
function sanitizeFileName(name) {
  return String(name)
    .replace(/[\\/:*?"<>| -]/g, '_')
    .replace(/\s+/g, '')
    .slice(0, 40) || 'XXX'
}

// 在 workDir（内含 1~4.csv）上运行 run_report.py 生成并打开 PDF
function runReport(workDir, rawName, rawWeight) {
  const name = (typeof rawName === 'string' && rawName.trim()) || 'XXX'
  const weight = Number(rawWeight) > 0 ? Number(rawWeight) : 80
  const stamp = path.basename(workDir).replace('walkway-', '')
  // 文件名用净化后的姓名（非法字符会导致保存 PDF 崩溃 Errno 22）；报告标题仍用原始姓名
  // 输出到 reports 目录（非临时目录），生成后自动打开
  const outputPdf = path.join(getReportsDir(), `步道报告_${sanitizeFileName(name)}_${stamp}.pdf`)
  // 优先级：内置 runtime > 开发期指定的 WALKWAY_PYTHON > 系统 python
  const pythonBin = getPackagedPythonBinary() || process.env.WALKWAY_PYTHON || getDevVenvPython() || 'python'
  const env = getPackagedPythonEnv()
  const args = [
    runReportScript,
    '--input-dir', workDir,
    '--output', outputPdf,
    '--name', name,
    '--weight', String(weight),
  ]
  sendLog(`开始生成报告：姓名=${name}，体重=${weight}kg`, 'info')
  sendLog(`Python：${pythonBin}`, 'info')
  sendLog(`数据目录：${workDir}`, 'info')
  return new Promise((resolve) => {
    let stderr = ''
    let stdout = ''
    let buf = ''
    const flush = (chunk, level) => {
      buf += chunk
      const parts = buf.split(/\r?\n/)
      buf = parts.pop()
      for (const l of parts) { if (l.trim()) sendLog(l, level) }
    }
    const child = spawn(pythonBin, args, { env, cwd: pythonDir })
    child.stdout.on('data', (d) => { const t = d.toString(); stdout += t; flush(t, 'py') })
    child.stderr.on('data', (d) => { const t = d.toString(); stderr += t; flush(t, 'py-err') })
    child.on('error', (err) => {
      sendLog(`无法启动 Python: ${err.message}`, 'error')
      resolve({ ok: false, error: `无法启动 Python: ${err.message}` })
    })
    child.on('exit', (code) => {
      if (buf.trim()) sendLog(buf, 'py')
      if (code === 0 && fs.existsSync(outputPdf)) {
        sendLog(`✅ 报告生成完成：${outputPdf}`, 'success')
        shell.openPath(outputPdf)
        resolve({ ok: true, pdfPath: outputPdf })
      } else {
        const errMsg = stderr.trim() || stdout.trim() || `Python 退出码 ${code}`
        sendLog(`❌ 报告生成失败（退出码 ${code}）`, 'error')
        resolve({ ok: false, error: errMsg })
      }
    })
  })
}

/* ─────────────────────── 生命周期 ─────────────────────── */
app.whenReady().then(() => {
  registerIpc()
  registerAppProtocol()
  startDeviceServer()
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  stopDeviceServer()
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  stopDeviceServer()
})
