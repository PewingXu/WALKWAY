/**
 * BackendBridge - 后端 WebSocket 桥接（精简版）
 *
 * 连接 ws://localhost:19999，接收高频脚垫数据：
 *   { sitData: { foot1: { status, arr[4096], stamp, HZ }, foot2: {...}, ... } }
 *
 * 对 foot1~foot4 中 status==='online' && Array.isArray(arr) 的：
 *   emit `foot{N}Data`，回调参数为 (arr, stamp)，arr 为原始一维数组（长度 4096）。
 *
 * 断线后 3 秒自动重连。导出单例 backendBridge。
 */
class BackendBridge {
  constructor() {
    this.ws = null
    this.isConnected = false
    this.reconnectTimer = null
    this.backendUrl = 'ws://localhost:19999'

    this._listeners = {
      connect: [],
      disconnect: [],
      error: [],
      foot1Data: [],
      foot2Data: [],
      foot3Data: [],
      foot4Data: [],
      // 整帧事件：一条 sitData 消息里 4 块为同一时刻
      // ({ foot1: arr|null, foot2, foot3, foot4, stamp }) => void
      frame: [],
      deviceStatus: [], // ({ type, status }) => void
    }

    // 各脚垫在线状态
    this.deviceOnline = {}
  }

  /* ─── 事件系统 ─── */
  on(event, callback) {
    if (this._listeners[event]) {
      this._listeners[event].push(callback)
    }
    return () => this.off(event, callback)
  }

  off(event, callback) {
    if (this._listeners[event]) {
      this._listeners[event] = this._listeners[event].filter((cb) => cb !== callback)
    }
  }

  _emit(event, ...args) {
    if (this._listeners[event]) {
      this._listeners[event].forEach((cb) => {
        try {
          cb(...args)
        } catch (e) {
          console.error('[BackendBridge] listener error:', e)
        }
      })
    }
  }

  /* ─── 连接管理 ─── */
  connect(url) {
    if (url) this.backendUrl = url
    if (this.ws) this.disconnect()

    try {
      console.log(`[BackendBridge] Connecting to ${this.backendUrl}...`)
      this.ws = new WebSocket(this.backendUrl)

      this.ws.onopen = () => {
        this.isConnected = true
        console.log('[BackendBridge] Connected')
        this._emit('connect')
      }

      this.ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          this._handleMessage(msg)
        } catch (e) {
          // 非 JSON 数据，忽略
        }
      }

      this.ws.onclose = () => {
        this.isConnected = false
        console.log('[BackendBridge] Disconnected')
        this._emit('disconnect')
        // 3 秒自动重连
        this.reconnectTimer = setTimeout(() => this.connect(), 3000)
      }

      this.ws.onerror = (error) => {
        console.error('[BackendBridge] Error:', error)
        this._emit('error', error)
      }
    } catch (e) {
      console.error('[BackendBridge] Connection failed:', e)
      this.reconnectTimer = setTimeout(() => this.connect(), 3000)
    }
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.ws) {
      this.ws.onclose = null // 防止触发重连
      try {
        this.ws.close()
      } catch (e) {
        /* noop */
      }
      this.ws = null
    }
    this.isConnected = false
    this._emit('disconnect')
  }

  /* ─── 消息处理 ─── */
  _handleMessage(msg) {
    if (msg && msg.sitData && typeof msg.sitData === 'object') {
      const data = msg.sitData
      // 整帧：本条消息里 4 块为同一时刻，逐块收集原始一维数组或 null
      const frame = { foot1: null, foot2: null, foot3: null, foot4: null, stamp: null }
      ;['foot1', 'foot2', 'foot3', 'foot4'].forEach((type) => {
        const entry = data[type]
        if (!entry) return
        this._updateDeviceStatus(type, entry.status)
        if (entry.status === 'online' && Array.isArray(entry.arr)) {
          this._emit(`${type}Data`, entry.arr, entry.stamp)
          frame[type] = entry.arr
          if (frame.stamp == null && entry.stamp != null) frame.stamp = entry.stamp
        }
      })
      // 只要本帧至少有一块在线数据就派发整帧事件
      if (frame.foot1 || frame.foot2 || frame.foot3 || frame.foot4) {
        this._emit('frame', frame)
      }
    }
  }

  _updateDeviceStatus(type, status) {
    const prev = this.deviceOnline[type]
    this.deviceOnline[type] = status
    if (prev !== status) {
      this._emit('deviceStatus', { type, status })
    }
  }

  getDeviceStatus() {
    return { ...this.deviceOnline }
  }
}

// 导出单例
export const backendBridge = new BackendBridge()
export default BackendBridge
