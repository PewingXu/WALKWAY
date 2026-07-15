// preload.js —— 通过 contextBridge 暴露受限的主进程能力给渲染进程
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  // 应用版本
  getAppVersion: () => ipcRenderer.invoke('get-app-version'),

  // ====== 保存 CSV ======
  // 选择导出目录，返回 { canceled, path }
  selectExportDirectory: () => ipcRenderer.invoke('select-export-directory'),
  // 写入单个文本文件（utf8）。payload: { directoryPath, fileName, data, encoding }
  writeExportFile: (payload) => ipcRenderer.invoke('write-export-file', payload),

  // ====== 生成报告 ======
  // payload: { csv: { '1': string, '2': string, '3': string, '4': string }, name, weight }
  // 返回 { ok, pdfPath?, error? }
  generateReport: (payload) => ipcRenderer.invoke('generate-report', payload),

  // ====== 导入 CSV 生成报告 ======
  // 选择包含 1~4.csv 的目录，返回 { canceled, path }
  selectImportDirectory: () => ipcRenderer.invoke('select-import-directory'),
  // payload: { dir, name, weight } 返回 { ok, pdfPath?, error? }
  generateReportFromDir: (payload) => ipcRenderer.invoke('generate-report-from-dir', payload),

  // ====== 终端式日志 ======
  // 订阅主进程推送的日志（设备/报告生成/错误）。cb 收到 { ts, level, line }
  onAppLog: (callback) => {
    const handler = (_event, data) => callback(data)
    ipcRenderer.on('app-log', handler)
    return () => ipcRenderer.removeListener('app-log', handler)
  },

  // ====== 设备后端状态 ======
  // 返回 { ready, port, error? }
  getDeviceStatus: () => ipcRenderer.invoke('get-device-status'),
  // 保存登录页密钥为本地设备映射（写 serial.txt 并重启串口服务）
  // 返回 { ok, count?, mapping?, path?, error? }
  saveDeviceKey: (key) => ipcRenderer.invoke('save-device-key', { key }),
  // 监听设备后端事件（ready / error / log）
  onDeviceEvent: (callback) => {
    const handler = (_event, data) => callback(data)
    ipcRenderer.on('device-event', handler)
    return () => ipcRenderer.removeListener('device-event', handler)
  },
})
