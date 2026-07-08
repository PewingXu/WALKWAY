/**
 * logBus —— 全局日志总线（终端式日志面板的数据源）
 *
 * 汇聚两类日志：
 *   1. 主进程推送（设备服务、Python 报告生成、错误）——通过 window.electronAPI.onAppLog
 *   2. 前端本地事件（导入解析、保存、点击生成报告等）——通过 logBus.push
 */

const MAX_LINES = 800
const buffer = []
const listeners = new Set()
let started = false

function emit() {
  for (const cb of listeners) {
    try { cb(buffer) } catch (e) { /* ignore */ }
  }
}

export function pushLog(line, level = 'info') {
  const entry = { ts: Date.now(), level, line: String(line) }
  buffer.push(entry)
  if (buffer.length > MAX_LINES) buffer.splice(0, buffer.length - MAX_LINES)
  emit()
  return entry
}

export function clearLog() {
  buffer.length = 0
  emit()
}

export function getLog() {
  return buffer
}

export function subscribe(cb) {
  listeners.add(cb)
  cb(buffer)
  return () => listeners.delete(cb)
}

// 只初始化一次：把主进程日志接入总线
export function startLogBridge() {
  if (started) return
  started = true
  if (typeof window !== 'undefined' && window.electronAPI && window.electronAPI.onAppLog) {
    window.electronAPI.onAppLog((msg) => {
      if (!msg) return
      buffer.push({ ts: msg.ts || Date.now(), level: msg.level || 'info', line: String(msg.line || '') })
      if (buffer.length > MAX_LINES) buffer.splice(0, buffer.length - MAX_LINES)
      emit()
    })
    pushLog('日志已连接主进程', 'muted')
  } else {
    pushLog('（浏览器模式：无主进程日志）', 'muted')
  }
}
