/**
 * ============================================================================
 *  gaitSerialServer.js —— 足底压力步道独立串口设备服务
 * ============================================================================
 *
 * 逐函数忠实移植自参考蓝本
 *   C:\Users\xpr12\Desktop\laonianren-express\back-end\code\server\serialServer.js
 * 但只保留【4096 字节帧（脚垫 foot1~foot4）】这一条链路，删除了手套(HL/HR)、
 * 坐垫(sit)、SQLite/db、express/HTTP、multer、algorithms(COP)、aes 加密、
 * 云端回退、历史回放等步道用不到的一切。
 *
 * 由 Electron 主进程 fork() 启动（stdio 含 'ipc'）：
 *   - 启动成功后 process.send({ type:'ready', port:19999 })
 *   - 收到 { type:'shutdown' } 或 SIGTERM 时清理串口/WS 后退出
 *   - 出错 process.send({ type:'error', message })
 *
 * WS 协议（端口 19999，广播 JSON 文本）：
 *   { "sitData": { "foot1": {status, arr[4096], stamp, HZ}, ... } }   // 只含在线 tile
 *   { "macInfo": { "foot1": "<uniqueId>", ... } }                     // 可选，现场识别 MAC
 *
 * 免硬件测试模式（见文件末尾）：
 *   WALKWAY_MOCK=1            —— 合成移动高斯压力斑点，约 77Hz 广播
 *   WALKWAY_REPLAY=<目录>     —— 回放该目录下 1.csv~4.csv（读不到退回 MOCK）
 * ============================================================================
 */

const fs = require('fs')
const path = require('path')
const os = require('os')
const WebSocket = require('ws')
// serialport 是原生模块（二进制需匹配运行时 ABI）。仅在真实设备模式才加载，
// 使 MOCK / REPLAY 模式无需安装/编译原生模块即可运行。
let SerialPort, DelimiterParser
function ensureSerialport() {
  if (!SerialPort) {
    ({ SerialPort, DelimiterParser } = require('serialport'))
  }
}
const { getPort } = require('./serialport')
const { splitArr, BAUD_DEVICE_MAP } = require('./config')

// ============================================================================
// 区块 0：集中可调常量
// ============================================================================

// 地区方向开关：'gz'=广州（默认，走 flipFoot64x64Vertical）；'bj'=北京（保留分支，默认关闭）
const REGION = process.env.WALKWAY_REGION === 'bj' ? 'bj' : 'gz'

// WS 服务端口（前端与 Electron 主进程约定）
const WS_PORT = 19999

// 步道模式（gait）为固定采样类型，脚垫始终按步道处理
const SAMPLE_TYPE = '5'

// 帧到达前的原始数据清洗常量（移植自蓝本 4096 分支）
const RAW_ZERO_THRESHOLD = 8   // zeroBelowThreshold(pointArr, 8)
const RAW_ISLAND_MIN = 12      // removeSmallIslands64x64(pointArr, 12)

// 脚垫步道滤波配置（移植自蓝本 footFilterConfig.gait）
const footFilterConfig = {
  gait: {
    filterEnabled: true,
    filterThreshold: 10,   // 低压力阈值（原15；降到10以保留脚跟初触的低压接触）
    filterMinArea: 0,      // 面积过滤已关闭（0=不移除小连通域）；仅保留 filterThreshold 低压去噪
    optimizeEnabled: true, // 坏线补值开关（步道模式下单 tile 不补，合并后由前端处理）
    optimizeBad: 40,
    optimizeGood: 100,
  },
}

// 波特率探测候选（本服务只有脚垫，但保留探测流程以做帧长双重验证）
const BAUD_CANDIDATES = [3000000]
const BAUD_EXPECTED_FRAME_LENGTHS = {
  3000000: [4096], // 脚垫: 4096 字节帧
}

// 帧数据新鲜度阈值（ms），超过视为离线
const STALE_MS = 5000

// 分隔符 Buffer
const splitBuffer = Buffer.from(splitArr)

// ============================================================================
// 区块 1：矩阵方向处理（移植自蓝本 :249/:264/:282）
// ============================================================================

// 广州默认：沿水平轴翻转行顺序（上下翻转）
function flipFoot64x64Vertical(arr) {
  if (!Array.isArray(arr) || arr.length !== 4096) return arr
  const size = 64
  const out = new Array(arr.length)
  for (let r = 0; r < size; r++) {
    const srcRowStart = (size - 1 - r) * size
    const dstRowStart = r * size
    for (let c = 0; c < size; c++) {
      out[dstRowStart + c] = arr[srcRowStart + c]
    }
  }
  return out
}

// 北京设备线序专用：水平翻转（广州不用）
function flipFlatMatrixHorizontal(arr, size) {
  if (!Array.isArray(arr) || arr.length !== size * size) return arr
  const out = new Array(arr.length)
  for (let r = 0; r < size; r++) {
    const rowStart = r * size
    for (let c = 0; c < size; c++) {
      out[rowStart + c] = arr[rowStart + (size - 1 - c)]
    }
  }
  return out
}

// 北京设备线序专用：首行移到末行
function shiftFoot64x64FirstRowToLast(arr) {
  if (!Array.isArray(arr) || arr.length !== 4096) return arr
  const size = 64
  const out = new Array(arr.length)
  for (let r = 0; r < size - 1; r++) {
    const srcRowStart = (r + 1) * size
    const dstRowStart = r * size
    for (let c = 0; c < size; c++) {
      out[dstRowStart + c] = arr[srcRowStart + c]
    }
  }
  const firstRowStart = 0
  const lastRowStart = (size - 1) * size
  for (let c = 0; c < size; c++) {
    out[lastRowStart + c] = arr[firstRowStart + c]
  }
  return out
}

// ============================================================================
// 区块 2：去噪（移植自蓝本 :301/:309/:353/:399）
// ============================================================================

// 低压力置零
function zeroBelowThreshold(arr, threshold) {
  if (!Array.isArray(arr)) return arr
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] < threshold) arr[i] = 0
  }
  return arr
}

// 移除小连通域（8 邻域 BFS）
function removeSmallIslands64x64(arr, minSize = 9) {
  if (!Array.isArray(arr) || arr.length !== 4096) return arr
  const size = 64
  const visited = new Array(arr.length).fill(false)
  const dirs = [-1, 0, 1]
  for (let idx = 0; idx < arr.length; idx++) {
    if (visited[idx] || arr[idx] <= 0) continue
    const stack = [idx]
    const component = []
    visited[idx] = true
    while (stack.length) {
      const cur = stack.pop()
      component.push(cur)
      const r = Math.floor(cur / size)
      const c = cur - r * size
      for (let dr of dirs) {
        const nr = r + dr
        if (nr < 0 || nr >= size) continue
        for (let dc of dirs) {
          const nc = c + dc
          if (nc < 0 || nc >= size) continue
          if (dr === 0 && dc === 0) continue
          const ni = nr * size + nc
          if (!visited[ni] && arr[ni] > 0) {
            visited[ni] = true
            stack.push(ni)
          }
        }
      }
    }
    if (component.length < minSize) {
      for (let i = 0; i < component.length; i++) {
        arr[component[i]] = 0
      }
    }
  }
  return arr
}

/**
 * 对 64x64 脚垫数据进行去噪滤波（低压力置零 + 小连通域移除）
 * 与前端 denoiseMatrix 逻辑一致，但操作一维数组
 */
function denoiseFootData(arr, threshold, minArea) {
  if (!Array.isArray(arr) || arr.length !== 4096) return arr
  const size = 64
  // 步骤1：低压力置零
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] < threshold) arr[i] = 0
  }
  // 步骤2：BFS 连通域分析，移除小区域（minArea<=0 时跳过面积过滤，仅保留低压阈值去噪）
  if (minArea <= 0) return arr
  const visited = new Array(arr.length).fill(false)
  const dirs = [-1, 0, 1]
  for (let idx = 0; idx < arr.length; idx++) {
    if (visited[idx] || arr[idx] <= 0) continue
    const stack = [idx]
    const component = []
    visited[idx] = true
    while (stack.length) {
      const cur = stack.pop()
      component.push(cur)
      const r = Math.floor(cur / size)
      const c = cur - r * size
      for (const dr of dirs) {
        const nr = r + dr
        if (nr < 0 || nr >= size) continue
        for (const dc of dirs) {
          const nc = c + dc
          if (nc < 0 || nc >= size) continue
          if (dr === 0 && dc === 0) continue
          const ni = nr * size + nc
          if (!visited[ni] && arr[ni] > 0) {
            visited[ni] = true
            stack.push(ni)
          }
        }
      }
    }
    if (component.length < minArea) {
      for (const ci of component) arr[ci] = 0
    }
  }
  return arr
}

/**
 * 对 64x64 脚垫数据进行坏线补值（检测异常低值行/列，用相邻行/列插值修复）
 * 支持连续 1~2 行/列坏线。步道模式下不在单 tile 上调用（由前端合并后处理）。
 */
function zeroLineRepair64x64(arr, badThresh, goodThresh) {
  if (!Array.isArray(arr) || arr.length !== 4096) return arr
  const ROWS = 64, COLS = 64

  const rowSums = new Float32Array(ROWS)
  const colSums = new Float32Array(COLS)
  for (let r = 0; r < ROWS; r++) {
    let total = 0
    for (let c = 0; c < COLS; c++) total += arr[r * COLS + c]
    rowSums[r] = total
  }
  for (let c = 0; c < COLS; c++) {
    let total = 0
    for (let r = 0; r < ROWS; r++) total += arr[r * COLS + c]
    colSums[c] = total
  }

  // 修复坏行
  for (let r = 1; r < ROWS - 1; r++) {
    if (rowSums[r] >= badThresh) continue
    if (rowSums[r - 1] > goodThresh && rowSums[r + 1] > goodThresh) {
      for (let c = 0; c < COLS; c++) {
        arr[r * COLS + c] = (arr[(r - 1) * COLS + c] + arr[(r + 1) * COLS + c]) / 2
      }
    } else if (r + 2 < ROWS && rowSums[r + 1] < badThresh &&
               rowSums[r - 1] > goodThresh && rowSums[r + 2] > goodThresh) {
      for (let c = 0; c < COLS; c++) {
        const vPrev = arr[(r - 1) * COLS + c]
        const vNext = arr[(r + 2) * COLS + c]
        arr[r * COLS + c]       = vPrev * 2 / 3 + vNext * 1 / 3
        arr[(r + 1) * COLS + c] = vPrev * 1 / 3 + vNext * 2 / 3
      }
      r++
    }
  }

  // 修复坏列
  for (let c = 1; c < COLS - 1; c++) {
    if (colSums[c] >= badThresh) continue
    if (colSums[c - 1] > goodThresh && colSums[c + 1] > goodThresh) {
      for (let r = 0; r < ROWS; r++) {
        arr[r * COLS + c] = (arr[r * COLS + (c - 1)] + arr[r * COLS + (c + 1)]) / 2
      }
    } else if (c + 2 < COLS && colSums[c + 1] < badThresh &&
               colSums[c - 1] > goodThresh && colSums[c + 2] > goodThresh) {
      for (let r = 0; r < ROWS; r++) {
        const vPrev = arr[r * COLS + (c - 1)]
        const vNext = arr[r * COLS + (c + 2)]
        arr[r * COLS + c]       = vPrev * 2 / 3 + vNext * 1 / 3
        arr[r * COLS + (c + 1)] = vPrev * 1 / 3 + vNext * 2 / 3
      }
      c++
    }
  }
  return arr
}

// ============================================================================
// 区块 3：滤波入口（移植自蓝本 applyFootFilter :462）
// ============================================================================

/**
 * 根据评估模式对脚垫数据应用滤波和坏线补值
 * @param {number[]} arr - 4096 长度一维数组
 * @param {string} mode - 本服务固定为 'gait'
 */
function applyFootFilter(arr, mode /*, footType */) {
  const cfg = footFilterConfig[mode]
  if (!cfg) { console.log('[applyFootFilter] no cfg for mode:', mode); return arr }
  if (cfg.filterEnabled) {
    denoiseFootData(arr, cfg.filterThreshold, cfg.filterMinArea)
  }
  if (cfg.optimizeEnabled) {
    if (mode === 'gait') {
      // 步道模式：坏线补值由前端 GaitCanvas 在合并 64×256 后处理，这里不动单 tile
    } else {
      zeroLineRepair64x64(arr, cfg.optimizeBad, cfg.optimizeGood)
    }
  }
  return arr
}

// ============================================================================
// 区块 4：MAC → footN 映射（移植自蓝本 :479/:532/:545/:681）
// serial.txt 缓存里的 key 字段是 { "<UniqueId>": "footN", ... } 映射
// 【移植改动】去掉了命中失败后请求云端的回退；未识别则临时按发现顺序分配 foot1..4，
// 并在日志/WS 里报告 Unique ID，方便现场把 MAC 抄进 serial.txt。
// ============================================================================

// serial.txt 路径候选（脚本目录优先，其次 userData）
const serialPathCandidates = (() => {
  const list = []
  if (process.env.WALKWAY_SERIAL_TXT) list.push(process.env.WALKWAY_SERIAL_TXT)
  list.push(path.join(__dirname, 'serial.txt'))
  if (process.env.userData) list.push(path.join(process.env.userData, 'serial.txt'))
  return list
})()

function readSerialCache() {
  for (const serialPath of serialPathCandidates) {
    try {
      if (!fs.existsSync(serialPath)) continue
      const raw = fs.readFileSync(serialPath, 'utf-8').trim()
      if (!raw) continue
      try {
        return JSON.parse(raw)
      } catch {
        return { key: raw }
      }
    } catch {
      // 尝试下一个候选
    }
  }
  return null
}

function parseSerialTypeMap(raw) {
  if (!raw) return {}
  if (typeof raw === 'object' && !Array.isArray(raw)) return raw
  if (typeof raw !== 'string') return {}
  let text = raw.trim()
  if (!text) return {}

  // 云端下发格式：{ "key": <map>, "orgName": ... }，抽出 key 段
  if (text.includes('"key"') && text.includes('"orgName"')) {
    const keyIdx = text.indexOf('"key"')
    if (keyIdx !== -1) {
      const afterKey = text.slice(keyIdx)
      const colonIdx = afterKey.indexOf(':')
      if (colonIdx !== -1) {
        let rest = afterKey.slice(colonIdx + 1)
        const orgIdx = rest.indexOf('"orgName"')
        if (orgIdx !== -1) rest = rest.slice(0, orgIdx)
        rest = rest.replace(/^[\s,]+/, '').replace(/[\s,]+$/, '')
        if (
          (rest.startsWith('"') && rest.endsWith('"')) ||
          (rest.startsWith("'") && rest.endsWith("'"))
        ) {
          rest = rest.slice(1, -1)
        }
        text = rest.trim()
      }
    }
  }

  const tryParse = (value) => {
    try {
      const obj = JSON.parse(value)
      if (obj && typeof obj === 'object' && !Array.isArray(obj)) return obj
    } catch { }
    return null
  }

  let obj = tryParse(text)
  if (obj) return obj

  const normalized = text.replace(/'/g, '"')
  obj = tryParse(normalized)
  if (obj) return obj

  const map = {}
  normalized.split(/[,;\n]+/).forEach((part) => {
    const m = part.match(/^\s*"?([^":=]+)"?\s*[:=]\s*"?([^"]+)"?\s*$/)
    if (m) {
      map[m[1].trim()] = m[2].trim()
    }
  })
  return map
}

function normalizeSerialIdentifier(value) {
  if (!value) return ''
  let text = String(value).trim().toUpperCase()
  const taggedMatch = text.match(/UNIQUE\s+ID:\s*([A-Z0-9]+)/i)
  if (taggedMatch && taggedMatch[1]) {
    return taggedMatch[1].trim().toUpperCase()
  }
  text = text.replace(/^UNIQUE\s+ID:\s*/i, '')
  text = text.split(/--|VERSIONS\s*:|COMPANY\s*:|\r|\n/i)[0] || text
  const idMatch = text.match(/[A-Z0-9]+/)
  return idMatch ? idMatch[0].trim().toUpperCase() : ''
}

function getTypeFromSerialCache(uniqueId) {
  if (!uniqueId) return null
  const cache = readSerialCache()
  const map = parseSerialTypeMap(cache && cache.key)
  const target = normalizeSerialIdentifier(uniqueId)
  if (!target) return null
  for (const key of Object.keys(map || {})) {
    if (normalizeSerialIdentifier(key) === target) {
      return map[key]
    }
  }
  return null
}

// ─── 未识别 MAC 的临时 foot 分配（移植改动：替代云端回退）───
const TEMP_FOOT_SLOTS = ['foot1', 'foot2', 'foot3', 'foot4']
function assignTemporaryFootType() {
  const used = new Set(Object.keys(dataMap).map((k) => dataMap[k].type).filter(Boolean))
  for (const slot of TEMP_FOOT_SLOTS) {
    if (!used.has(slot)) return slot
  }
  return null // 4 块都占满了
}

// ============================================================================
// 区块 5：波特率探测（移植自蓝本 :1119-1267）
// ============================================================================

function bufferContainsSequence(buffer, sequence) {
  if (!buffer || buffer.length < sequence.length) return false
  for (let i = 0; i <= buffer.length - sequence.length; i++) {
    let match = true
    for (let j = 0; j < sequence.length; j++) {
      if (buffer[i + j] !== sequence[j]) { match = false; break }
    }
    if (match) return true
  }
  return false
}

/**
 * 从 buffer 中提取分隔符切割后的第一个完整帧的长度
 * 返回帧长度，或 -1 表示未找到完整帧
 */
function extractFrameLength(buffer, sequence) {
  if (!buffer || buffer.length < sequence.length) return -1
  let firstDelim = -1
  for (let i = 0; i <= buffer.length - sequence.length; i++) {
    let match = true
    for (let j = 0; j < sequence.length; j++) {
      if (buffer[i + j] !== sequence[j]) { match = false; break }
    }
    if (match) { firstDelim = i; break }
  }
  if (firstDelim < 0) return -1
  const dataStart = firstDelim + sequence.length
  for (let i = dataStart; i <= buffer.length - sequence.length; i++) {
    let match = true
    for (let j = 0; j < sequence.length; j++) {
      if (buffer[i + j] !== sequence[j]) { match = false; break }
    }
    if (match) {
      return i - dataStart // 两个分隔符之间的数据长度就是帧长度
    }
  }
  return -1 // 只找到一个分隔符，没有完整帧
}

async function detectBaudRate(portPath, timeoutMs = 1500, maxRetries = 2) {
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    if (attempt > 0) {
      console.log(`[baud] ${portPath} retry #${attempt}`)
      await new Promise(r => setTimeout(r, 500))
    }
    for (let i = 0; i < BAUD_CANDIDATES.length; i++) {
      const baudRate = BAUD_CANDIDATES[i]
      const expectedLengths = BAUD_EXPECTED_FRAME_LENGTHS[baudRate] || []

      const result = await new Promise((resolve) => {
        let cache = Buffer.alloc(0)
        let timer = null
        let port = null
        let resolved = false
        let delimiterFound = false

        const cleanup = (res) => {
          if (resolved) return
          resolved = true
          if (timer) clearTimeout(timer)
          if (port) {
            port.off('data', onData)
            port.off('error', onError)
            if (port.isOpen) {
              port.close(() => resolve(res))
              return
            }
          }
          resolve(res)
        }

        const onData = (data) => {
          cache = Buffer.concat([cache, Buffer.from(data)])
          // 限制缓存大小，保留足够数据用于帧长度检测（最大帧 4096 + 分隔符开销）
          if (cache.length > 12288) {
            cache = cache.slice(-12288)
          }
          // 第一步：检测分隔符
          if (!delimiterFound && bufferContainsSequence(cache, splitArr)) {
            delimiterFound = true
            console.log(`[baud] ${portPath} @${baudRate} delimiter found, checking frame length...`)
          }
          // 第二步：检测帧长度是否匹配
          if (delimiterFound) {
            const frameLen = extractFrameLength(cache, splitArr)
            if (frameLen > 0) {
              if (expectedLengths.includes(frameLen)) {
                console.log(`[baud] ${portPath} @${baudRate} frame length ${frameLen} matches!`)
                cleanup('match')
              } else {
                console.log(`[baud] ${portPath} @${baudRate} frame length ${frameLen} does NOT match expected ${JSON.stringify(expectedLengths)}`)
                cleanup('mismatch')
              }
            }
          }
        }

        const onError = (err) => {
          console.log(`[baud] ${portPath} @${baudRate} error:`, err?.message || err)
          cleanup('error')
        }

        try {
          // serialport v13 正确构造
          port = new SerialPort({ path: portPath, baudRate, autoOpen: true })
          port.on('data', onData)
          port.on('error', onError)
        } catch (e) {
          console.log(`[baud] ${portPath} @${baudRate} open failed:`, e?.message || e)
          cleanup('error')
          return
        }

        timer = setTimeout(() => {
          if (delimiterFound) {
            console.log(`[baud] ${portPath} @${baudRate} delimiter found but frame length not verified (timeout), accepting`)
            cleanup('match')
          } else {
            cleanup('timeout')
          }
        }, timeoutMs)
      })

      // 每次尝试后等待端口锁释放（macOS 需要时间释放文件锁）
      await new Promise(r => setTimeout(r, 300))

      if (result === 'match') return baudRate
    }
  }
  return null
}

// ============================================================================
// 区块 6：AT 指令取 MAC（移植自蓝本 portWirte :2983 / sendMacCommand :3001）
// ============================================================================

function portWirte(port) {
  return new Promise((resolve, reject) => {
    // AT+NAME=ESP32\r\n  的 hex 编码（与蓝本完全一致）
    const command = Buffer.from('41542B4E414D453D45535033320d0a', 'hex')
    port.write(command, err => {
      if (err) {
        console.error('[AT] write err:', err.message)
        return reject(err)
      }
      resolve(11)
    })
  })
}

let sendMacNum = 0, successNum = 0

function sendMacCommand(port, portPath, baudRate, parserItem) {
  if (!port) return
  const run = () => {
    if (baudRate === 3000000) {
      if (parserItem?.macTimer) return
      const sendOnce = () => {
        portWirte(port)
          .then(() => {
            sendMacNum++
            console.log(`[sendAT] ${portPath} total=${sendMacNum} success=${successNum}`)
          })
          .catch((err) => {
            console.log(`[sendAT] ${portPath} failed`, err && err.message)
          })
      }
      sendOnce()
      // 3M 脚垫：轮询发 AT 直到收到 MAC 回帧
      parserItem.macTimer = setInterval(() => {
        if (parserItem.macReady) {
          clearInterval(parserItem.macTimer)
          parserItem.macTimer = null
          return
        }
        sendOnce()
      }, 300)
    }
  }
  if (port.isOpen) {
    run()
  } else {
    port.once('open', run)
  }
}

// ============================================================================
// 区块 7：WebSocket 服务与广播（移植自蓝本 :3441/:3543）
// ============================================================================

let server = null

// 向所有已连接客户端广播（移植自蓝本 socketSendData）
function socketSendData(data) {
  if (!server) return
  server.clients.forEach(function each(client) {
    if (client.readyState === WebSocket.OPEN) {
      client.send(data)
    }
  })
}

// 推送 macInfo（可选，现场识别 MAC）
function pushMacInfoUpdate() {
  try {
    const out = {}
    for (const p of Object.keys(macInfo)) {
      const info = macInfo[p] || {}
      const type = (dataMap[p] && dataMap[p].type) || info.type
      if (type && info.uniqueId) out[type] = info.uniqueId
    }
    if (Object.keys(out).length) {
      socketSendData(JSON.stringify({ macInfo: out }))
    }
  } catch {}
}

// ============================================================================
// 区块 8：运行时状态
// ============================================================================

let baudRate = 3000000       // 默认波特率（脚垫）
let parserArr = {}           // path -> { port, parser, baudRate, macReady, macTimer }
let dataMap = {}             // path -> { type, premission, arr, stamp, HZ, intervalMs }
let macInfo = {}             // path -> { uniqueId, version, type }
let HZ = 30, MaxHZ           // 广播频率（由首若干帧测定）
let sendDataLength = 0
let playtimer = null         // 定时广播计时器
const oldTimeObj = {}        // type -> 上一帧 stamp，用于测帧间隔

// ============================================================================
// 区块 9：数据组装（移植自蓝本 parseData :3656 / sendData :4398），只保留脚垫
// ============================================================================

// 把 dataMap 组装成 { footN: {status,arr,stamp,HZ} }，只含在线 tile
function parseData() {
  const json = {}
  Object.keys(dataMap).forEach((key) => {
    const obj = parserArr[key]
    const data = dataMap[key]
    if (!data || !data.type) return
    if (!obj || !obj.port || !obj.port.isOpen) return
    const arr = data.arr && data.arr.length ? data.arr : []
    const dataStamp = Date.now() - (data.stamp || 0)
    if (dataStamp < STALE_MS && arr.length === 4096) {
      json[data.type] = {
        status: 'online',
        arr,                       // 已翻转+滤波后的一维 4096（不转置）
        stamp: data.stamp,
        HZ: data.HZ || HZ,
      }
    }
  })
  return json
}

// 组装并广播一次
function colAndSendData() {
  if (!Object.keys(parserArr).length) return
  const obj = parseData()
  if (obj && Object.keys(obj).length) {
    socketSendData(JSON.stringify({ sitData: obj }))
  }
}

// ============================================================================
// 区块 10：主串口处理循环（移植自蓝本 connectPort :3769 / 4096 分支 :4178）
// ============================================================================

async function connectPort() {
  ensureSerialport()
  macInfo = {}
  let ports = await SerialPort.list()
  ports = getPort(ports)
  console.log('[phase1] found', ports.length, 'candidate ports')

  // ── 阶段一：逐个探测波特率（每次只开一个端口，避免 CH340 端口锁冲突）──
  const baudDetectResults = {}
  for (let i = 0; i < ports.length; i++) {
    const { path: portPath } = ports[i]
    const detectedBaud = await detectBaudRate(portPath)
    baudDetectResults[portPath] = detectedBaud
    console.log('[phase1]', portPath, '=>', detectedBaud || 'null (skip, not foot)')
    await new Promise(r => setTimeout(r, 500))
  }
  // 等待端口锁彻底释放
  await new Promise(r => setTimeout(r, 1000))

  // ── 阶段二：逐个打开端口、建立连接、发 AT 取 MAC、挂帧处理 ──
  console.log('[phase2] connecting ports')
  for (let i = 0; i < ports.length; i++) {
    const portInfo = ports[i]
    const { path: portPath } = portInfo
    const detectedBaud = baudDetectResults[portPath]
    // 只保留脚垫链路：未探测到 3M 的端口直接跳过
    if (detectedBaud !== 3000000) {
      console.log('[phase2]', portPath, 'skipped: not a foot mat (3M)')
      continue
    }
    const portBaudRate = detectedBaud

    const parserItem = parserArr[portPath] = parserArr[portPath] || {}
    const dataItem = dataMap[portPath] = dataMap[portPath] || {}
    parserItem.baudRate = portBaudRate
    parserItem.parser = new DelimiterParser({ delimiter: splitBuffer })
    const { parser } = parserItem

    if (parserItem.port && parserItem.port.isOpen) continue

    // 3M → 设备大类 foot（foot1-4 稍后由 MAC 细分）
    if (BAUD_DEVICE_MAP[portBaudRate] === 'foot') {
      dataItem.type = 'foot'
      console.log('[device]', portPath, '=> foot (by baud', portBaudRate, ')')
    }

    const port = await newSerialPortLinkWithRetry({ path: portPath, parser, baudRate: portBaudRate })
    if (!port) {
      console.log('[port]', portPath, 'skipped: unable to open')
      continue
    }
    parserItem.port = port

    // 连接成功 → 发 AT 指令取 MAC
    sendMacCommand(port, portPath, portBaudRate, parserItem)

    // 帧处理
    parser.on('data', function (data) {
      const buffer = Buffer.from(data)

      // ── MAC 回帧解析（Unique ID）：移植自蓝本 :3936 ──
      if (buffer.length !== 4096 && buffer.toString().includes('Unique ID')) {
        const str = buffer.toString()
        const uniqueIdMatch = str.match(/Unique ID:\s*([A-Za-z0-9]+)/i)
        const versionMatch = str.match(/Versions:\s*([A-Za-z0-9._-]+)/i)
        const uniqueId = uniqueIdMatch ? uniqueIdMatch[1].trim() : null
        const version = versionMatch ? versionMatch[1].trim() : null
        console.log(`[mac] ${portPath} Unique ID: ${uniqueId || 'n/a'} Versions: ${version || 'n/a'}`)

        successNum++
        parserItem.macReady = true
        if (parserItem.macTimer) {
          clearInterval(parserItem.macTimer)
          parserItem.macTimer = null
        }
        macInfo[portPath] = { uniqueId, version }

        // 脚垫：查 serial.txt 映射表确定 foot1-4；未命中则临时分配并报告
        const mappedType = getTypeFromSerialCache(uniqueId)
        if (mappedType) {
          dataItem.type = String(mappedType).trim()
          dataItem.premission = true
          macInfo[portPath].type = dataItem.type
          console.log(`[foot] ${portPath} MAC=${uniqueId} => ${dataItem.type}`)
        } else {
          const temp = assignTemporaryFootType()
          if (temp) {
            dataItem.type = temp
            dataItem.premission = true
            macInfo[portPath].type = temp
            console.log(`[foot] ${portPath} MAC=${uniqueId} 未在 serial.txt 命中，临时分配 => ${temp}`)
            console.log(`[foot] 请把该行加入 serial.txt: "${uniqueId}": "${temp}"`)
          } else {
            console.log(`[foot] ${portPath} MAC=${uniqueId} 未命中且 4 块 foot 槽已占满`)
          }
        }
        pushMacInfoUpdate()
        return
      }

      // ── 4096 字节帧（脚垫 64×64）：移植自蓝本 :4178 ──
      if (buffer.length !== 4096) return

      const pointArr = new Array(4096)
      for (let i = 0; i < buffer.length; i++) {
        pointArr[i] = buffer.readUInt8(i)
      }

      dataItem.premission = true
      if (!dataItem.type) dataItem.type = 'foot'

      // 原始清洗（面积过滤已关闭：不移除小连通域，仅保留低压阈值去噪）
      zeroBelowThreshold(pointArr, RAW_ZERO_THRESHOLD)
      // removeSmallIslands64x64(pointArr, RAW_ISLAND_MIN) // 已关闭面积过滤

      // 方向归一化：默认走广州逻辑；北京分支保留但由 REGION 开关关闭
      let flippedArr
      if (REGION === 'bj') {
        // 北京：先首行移末行，步道(SAMPLE_TYPE='5')仅上下翻
        flippedArr = shiftFoot64x64FirstRowToLast(pointArr)
        if (SAMPLE_TYPE === '3' || SAMPLE_TYPE === '4') {
          flippedArr = flipFoot64x64Vertical(flippedArr)
          flippedArr = flipFlatMatrixHorizontal(flippedArr, 64)
        } else if (SAMPLE_TYPE === '5') {
          flippedArr = flipFoot64x64Vertical(flippedArr)
        }
      } else {
        // 广州（默认）：上下翻转
        flippedArr = flipFoot64x64Vertical(pointArr)
      }

      // 步道滤波
      applyFootFilter(flippedArr, 'gait', dataItem.type)

      dataItem.arr = flippedArr
      const stamp = Date.now()

      // ── HZ 测定 + 定时广播：移植自蓝本 :4225 ──
      if (sendDataLength < 30) sendDataLength++
      if (oldTimeObj[dataItem.type]) {
        dataItem.intervalMs = stamp - oldTimeObj[dataItem.type]
        // HZ 为测得帧率（每秒帧数），符合 WS 协议 "HZ":77
        if (dataItem.intervalMs > 0) {
          dataItem.HZ = Math.round(1000 / dataItem.intervalMs)
        }
        if (!MaxHZ && sendDataLength === 30) {
          MaxHZ = Math.floor(1000 / dataItem.intervalMs)
          HZ = MaxHZ || 30
          console.log('[hz] locked broadcast HZ =', HZ)
          if (!playtimer) {
            playtimer = setInterval(colAndSendData, 1000 / HZ)
          }
          sendDataLength = 0
        }
      }
      dataItem.stamp = stamp
      oldTimeObj[dataItem.type] = stamp
    })
  }

  // 若探测阶段就没测到帧率（例如设备还没稳定），也起一个兜底广播计时器
  if (!playtimer && Object.keys(parserArr).length) {
    playtimer = setInterval(colAndSendData, 1000 / HZ)
  }

  return ports
}

/**
 * 带重试的串口连接（移植自蓝本 :3601），解决 detectBaudRate 关闭端口后
 * 系统未及时释放文件锁导致的 "Cannot lock port"
 */
async function newSerialPortLinkWithRetry({ path: portPath, parser, baudRate = 3000000, maxRetries = 3, retryDelay = 500 }) {
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    if (attempt > 0) {
      console.log(`[port] ${portPath} retry #${attempt} after ${retryDelay}ms`)
      await new Promise(r => setTimeout(r, retryDelay))
    }
    let port
    try {
      // serialport v13 正确构造
      port = new SerialPort({ path: portPath, baudRate, autoOpen: true })
      port.pipe(parser)
    } catch (e) {
      console.log('[port] open exception:', e && e.message)
      continue
    }
    const opened = await new Promise((resolve) => {
      if (port.isOpen) return resolve(true)
      const onOpen = () => { port.off('error', onErr); resolve(true) }
      const onErr = (err) => {
        port.off('open', onOpen)
        if (err && /lock|unavailable|EBUSY/i.test(err.message)) {
          console.log(`[port] ${portPath} lock error on attempt ${attempt}:`, err.message)
          resolve(false)
        } else {
          resolve(true)
        }
      }
      port.once('open', onOpen)
      port.once('error', onErr)
      setTimeout(() => {
        port.off('open', onOpen)
        port.off('error', onErr)
        resolve(port.isOpen)
      }, 2000)
    })
    if (opened) {
      console.log(`[port] ${portPath} opened successfully` + (attempt > 0 ? ` (after ${attempt} retries)` : ''))
      return port
    }
    try { if (port.isOpen) port.close() } catch {}
  }
  console.log(`[port] ${portPath} failed to open after ${maxRetries} retries`)
  return null
}

// 关闭所有串口、清理计时器（移植自蓝本 stopPort :4280）
function stopPort() {
  for (const p of Object.keys(parserArr)) {
    const item = parserArr[p]
    if (item && item.macTimer) { clearInterval(item.macTimer); item.macTimer = null }
    if (item && item.port && item.port.isOpen) {
      try { item.port.close() } catch {}
    }
  }
  parserArr = {}
  dataMap = {}
  if (playtimer) { clearInterval(playtimer); playtimer = null }
  MaxHZ = undefined
  sendDataLength = 0
}

// ============================================================================
// 区块 11：免硬件测试模式（MOCK / REPLAY）
// ============================================================================

let mockTimer = null

// 生成一帧移动高斯压力斑点（64×64 一维）
function makeGaussianFrame(t, tileIndex) {
  const size = 64
  const arr = new Array(size * size).fill(0)
  // 斑点随时间在 tile 内上下移动，模拟脚步在步道上移动
  const phase = (t / 1000 + tileIndex * 0.5)
  const cy = 12 + ((Math.sin(phase) * 0.5 + 0.5) * (size - 24))
  const cx = 32 + Math.sin(phase * 0.7) * 8
  const sigma = 6
  const peak = 220
  for (let r = 0; r < size; r++) {
    for (let c = 0; c < size; c++) {
      const d2 = (r - cy) * (r - cy) + (c - cx) * (c - cx)
      const v = Math.round(peak * Math.exp(-d2 / (2 * sigma * sigma)))
      arr[r * size + c] = v > 6 ? v : 0
    }
  }
  return arr
}

function startMockMode() {
  console.log('[mock] WALKWAY_MOCK 模式：合成 4 块脚垫压力帧，约 77Hz 广播')
  const targetHz = 77
  HZ = targetHz
  const start = Date.now()
  mockTimer = setInterval(() => {
    const t = Date.now() - start
    const stamp = Date.now()
    const sitData = {}
    for (let k = 0; k < 4; k++) {
      const arr = makeGaussianFrame(t, k)
      applyFootFilter(arr, 'gait', `foot${k + 1}`)
      sitData[`foot${k + 1}`] = { status: 'online', arr, stamp, HZ: targetHz }
    }
    socketSendData(JSON.stringify({ sitData }))
  }, Math.round(1000 / targetHz))
}

// 解析 CSV 里的 data 字段（形如 "[1, 2, 3, ...]" 的 list 字面量字符串）
function parseListLiteral(str) {
  if (!str) return null
  try {
    const cleaned = String(str).trim().replace(/^"+|"+$/g, '')
    const nums = cleaned.replace(/[\[\]]/g, '').split(',')
      .map((s) => parseInt(s.trim(), 10))
      .filter((n) => !Number.isNaN(n))
    return nums.length === 4096 ? nums : null
  } catch {
    return null
  }
}

// 极简 CSV 读取：返回 [{data, time, max}]，处理带引号包裹的 list 字段
function readReplayCsv(file) {
  const raw = fs.readFileSync(file, 'utf-8')
  const lines = raw.split(/\r?\n/).filter((l) => l.trim())
  if (!lines.length) return []
  const header = lines[0].split(',').map((h) => h.trim().toLowerCase())
  const di = header.indexOf('data')
  const ti = header.indexOf('time')
  const rows = []
  for (let i = 1; i < lines.length; i++) {
    // data 字段可能含逗号且被引号包裹，需按引号切分
    const line = lines[i]
    const cells = splitCsvLine(line)
    if (!cells.length) continue
    const dataStr = cells[di]
    const timeStr = cells[ti] || String(i)
    const arr = parseListLiteral(dataStr)
    if (arr) rows.push({ arr, time: Number(timeStr) || i })
  }
  return rows
}

// CSV 行切分，支持双引号包裹字段内的逗号
function splitCsvLine(line) {
  const out = []
  let cur = ''
  let inQuote = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (ch === '"') { inQuote = !inQuote; continue }
    if (ch === ',' && !inQuote) { out.push(cur); cur = ''; continue }
    cur += ch
  }
  out.push(cur)
  return out
}

function startReplayMode(dir) {
  const tiles = []
  for (let k = 1; k <= 4; k++) {
    const file = path.join(dir, `${k}.csv`)
    if (fs.existsSync(file)) {
      try {
        const rows = readReplayCsv(file)
        if (rows.length) { tiles[k - 1] = rows; continue }
      } catch (e) {
        console.log(`[replay] 读取 ${file} 失败:`, e.message)
      }
    }
    tiles[k - 1] = null
  }
  const hasAny = tiles.some((t) => t && t.length)
  if (!hasAny) {
    console.log('[replay] 未找到可用的 1.csv~4.csv，退回 MOCK 模式')
    startMockMode()
    return
  }
  console.log('[replay] 回放模式，帧数:', tiles.map((t) => (t ? t.length : 0)).join('/'))
  const targetHz = 77
  HZ = targetHz
  const idx = [0, 0, 0, 0]
  mockTimer = setInterval(() => {
    const stamp = Date.now()
    const sitData = {}
    for (let k = 0; k < 4; k++) {
      const rows = tiles[k]
      if (!rows || !rows.length) continue
      const row = rows[idx[k] % rows.length]
      idx[k]++
      sitData[`foot${k + 1}`] = { status: 'online', arr: row.arr, stamp, HZ: targetHz }
    }
    if (Object.keys(sitData).length) {
      socketSendData(JSON.stringify({ sitData }))
    }
  }, Math.round(1000 / targetHz))
}

// ============================================================================
// 区块 12：启动、IPC、优雅退出
// ============================================================================

function notifyReady() {
  console.log(`[ready] gait serial server ws://0.0.0.0:${WS_PORT}`)
  if (process.send) {
    try { process.send({ type: 'ready', port: WS_PORT }) } catch {}
  }
}

function notifyError(message) {
  console.error('[error]', message)
  if (process.send) {
    try { process.send({ type: 'error', message: String(message) }) } catch {}
  }
}

function startWsServer() {
  server = new WebSocket.Server({ port: WS_PORT })
  server.on('connection', (ws) => {
    console.log('[ws] client connected, total =', server.clients.size)
    // 新客户端连上立即推送一次 macInfo，便于现场识别
    pushMacInfoUpdate()
  })
  server.on('error', (err) => notifyError('ws server error: ' + (err && err.message)))
}

let shuttingDown = false
function shutdown(code = 0) {
  if (shuttingDown) return
  shuttingDown = true
  console.log('[shutdown] cleaning up...')
  try { if (mockTimer) { clearInterval(mockTimer); mockTimer = null } } catch {}
  try { stopPort() } catch {}
  try {
    if (server) {
      server.clients.forEach((c) => { try { c.terminate() } catch {} })
      server.close()
    }
  } catch {}
  setTimeout(() => process.exit(code), 200)
}

async function main() {
  try {
    startWsServer()

    const mock = process.env.WALKWAY_MOCK === '1'
    const replayDir = process.env.WALKWAY_REPLAY

    if (replayDir) {
      startReplayMode(replayDir)
    } else if (mock) {
      startMockMode()
    } else {
      // 真实硬件链路
      await connectPort()
    }

    notifyReady()
  } catch (e) {
    notifyError(e && e.stack ? e.stack : e)
    // 起不来也别静默：WS 若已起来则继续，否则退出
  }
}

// IPC：主进程发来的控制消息
process.on('message', (msg) => {
  if (!msg || typeof msg !== 'object') return
  if (msg.type === 'shutdown') {
    shutdown(0)
  }
})

// 信号：SIGTERM / SIGINT 优雅退出
process.on('SIGTERM', () => shutdown(0))
process.on('SIGINT', () => shutdown(0))

process.on('uncaughtException', (err) => {
  notifyError('uncaughtException: ' + (err && err.stack ? err.stack : err))
})
process.on('unhandledRejection', (reason) => {
  notifyError('unhandledRejection: ' + reason)
})

main()
