import { useEffect, useRef, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { backendBridge } from '../lib/BackendBridge.js'
import { buildCsvRow, buildCsv } from '../lib/csvExport.js'
import { buildWalkwayXlsxBase64, fileStampFromTs } from '../lib/xlsxExport.js'
import { parseWorkbookArrayBuffer } from '../lib/dataImport.js'
import { setReplayFrames } from '../lib/replayStore.js'
import HeatmapGrid from '../components/HeatmapGrid.jsx'
import GaitWalkway from '../components/GaitWalkway.jsx'
import LogConsole from '../components/LogConsole.jsx'
import { pushLog } from '../lib/logBus.js'

const PADS = [1, 2, 3, 4]

export default function Capture() {
  const navigate = useNavigate()

  // 每块最新帧（转置后的 64x64 矩阵），用于热力图显示
  const [matrices, setMatrices] = useState({ 1: null, 2: null, 3: null, 4: null })
  // 各脚垫在线状态
  const [padOnline, setPadOnline] = useState({ 1: false, 2: false, 3: false, 4: false })
  // WS 自身连接状态
  const [wsConnected, setWsConnected] = useState(false)
  // 设备后端状态（electronAPI.getDeviceStatus / onDeviceEvent）
  const [deviceReady, setDeviceReady] = useState(false)

  // 受试者信息
  const [name, setName] = useState('')
  const [weight, setWeight] = useState('')

  // 采集状态
  const [isRecording, setIsRecording] = useState(false)
  const [elapsed, setElapsed] = useState(0) // 秒
  const [frameCount, setFrameCount] = useState({ 1: 0, 2: 0, 3: 0, 4: 0 })
  const [hasStopped, setHasStopped] = useState(false) // 停止过一次后显示保存/报告按钮
  const [reportLoading, setReportLoading] = useState(false)
  const [toast, setToast] = useState('')
  // 主视图模式：'walkway' 一整条步道点云（默认）/ 'heatmap' 2x2 热力图
  const [viewMode, setViewMode] = useState('walkway')

  // refs：累积的 CSV 行（每块），采集开关，计时器
  const framesRef = useRef({ 1: [], 2: [], 3: [], 4: [] })
  // 按 WS 消息级累积的整帧行（用于导出 express 式宽表 xlsx）
  // 每项：{ ts, foot1: arr|null, foot2, foot3, foot4 }
  const rowsRef = useRef([])
  const isRecordingRef = useRef(false)
  const timerRef = useRef(null)
  const startTimeRef = useRef(0)
  // 隐藏的导入文件输入
  const importInputRef = useRef(null)

  const showToast = useCallback((msg) => {
    setToast(msg)
    window.clearTimeout(showToast._t)
    showToast._t = window.setTimeout(() => setToast(''), 3000)
  }, [])

  // 默认姓名候选：sessionStorage 里的 key
  useEffect(() => {
    try {
      const k = sessionStorage.getItem('wk_key')
      if (k) setName(k)
    } catch (e) {
      /* noop */
    }
  }, [])

  // ─── 连接 WS 与监听脚垫数据 ───
  useEffect(() => {
    const offConnect = backendBridge.on('connect', () => setWsConnected(true))
    const offDisconnect = backendBridge.on('disconnect', () => {
      setWsConnected(false)
      setPadOnline({ 1: false, 2: false, 3: false, 4: false })
    })
    const offStatus = backendBridge.on('deviceStatus', ({ type, status }) => {
      const m = /^foot([1-4])$/.exec(type)
      if (m) {
        const n = Number(m[1])
        setPadOnline((prev) => ({ ...prev, [n]: status === 'online' }))
      }
    })

    // 每块 foot{N}Data 处理器
    const offs = PADS.map((n) => {
      const handler = (arr) => {
        // 到达时刻（用于 CSV time）
        const arrivedAt = new Date()

        // 显示：reshape 64x64 再转置（对齐蓝本 GaitAssessment）
        const raw = []
        for (let r = 0; r < 64; r++) raw.push(arr.slice(r * 64, (r + 1) * 64))
        const matrix = []
        for (let c = 0; c < 64; c++) {
          const row = []
          for (let r = 0; r < 64; r++) row.push(raw[r][c])
          matrix.push(row)
        }
        setMatrices((prev) => ({ ...prev, [n]: matrix }))

        // 累积（采集中）：push 拼好的 CSV 行字符串（原始一维 arr）
        if (isRecordingRef.current) {
          framesRef.current[n].push(buildCsvRow(arr, arrivedAt))
          setFrameCount((prev) => ({ ...prev, [n]: framesRef.current[n].length }))
        }
      }
      return backendBridge.on(`foot${n}Data`, handler)
    })

    // 整帧事件：一条 WS 消息里 4 块为同一时刻，采集中时累积整行（原始一维 arr）
    const offFrame = backendBridge.on('frame', (frame) => {
      if (!isRecordingRef.current) return
      rowsRef.current.push({
        ts: Date.now(), // 该帧到达时刻（ms）
        foot1: frame.foot1 || null,
        foot2: frame.foot2 || null,
        foot3: frame.foot3 || null,
        foot4: frame.foot4 || null,
      })
    })

    backendBridge.connect()

    return () => {
      offConnect()
      offDisconnect()
      offStatus()
      offs.forEach((off) => off())
      offFrame()
      backendBridge.disconnect()
    }
  }, [])

  // ─── 设备后端状态（electronAPI）───
  useEffect(() => {
    const api = window.electronAPI
    if (!api) return
    let off = null
    api
      .getDeviceStatus()
      .then((s) => setDeviceReady(!!(s && s.ready)))
      .catch(() => {})
    if (api.onDeviceEvent) {
      off = api.onDeviceEvent((data) => {
        if (!data) return
        if (data.type === 'ready') setDeviceReady(true)
        else if (data.type === 'error' || data.type === 'exit') setDeviceReady(false)
      })
    }
    return () => {
      if (typeof off === 'function') off()
    }
  }, [])

  // ─── 计时器 ───
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [])

  const handleStart = () => {
    // 重置累积
    framesRef.current = { 1: [], 2: [], 3: [], 4: [] }
    rowsRef.current = []
    setFrameCount({ 1: 0, 2: 0, 3: 0, 4: 0 })
    setElapsed(0)
    setHasStopped(false)
    startTimeRef.current = Date.now()
    isRecordingRef.current = true
    setIsRecording(true)
    if (timerRef.current) clearInterval(timerRef.current)
    timerRef.current = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTimeRef.current) / 1000))
    }, 250)
  }

  const handleStop = () => {
    isRecordingRef.current = false
    setIsRecording(false)
    setHasStopped(true)
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }

  // ─── 保存为 express 式宽表 .xlsx（sheet "行走步态评估"）───
  const handleSaveXlsx = async () => {
    const api = window.electronAPI
    if (!api) {
      alert('请在桌面应用内使用')
      return
    }
    const rows = rowsRef.current
    if (!rows || rows.length === 0) {
      alert('无采集数据')
      return
    }
    try {
      const { canceled, path } = await api.selectExportDirectory()
      if (canceled) return
      const base64 = buildWalkwayXlsxBase64(rows)
      // 命名对齐 express「四项评估数据」：姓名_YYYY_MM_DD_HHMMSS_四项评估数据.xlsx
      // （本工具只采集步道，文件内只含「行走步态评估」这一页）
      const fileName = `${name || '步道'}_${fileStampFromTs(rows[0].ts)}_四项评估数据.xlsx`
      await api.writeExportFile({
        directoryPath: path,
        fileName,
        data: base64,
        encoding: 'base64',
      })
      const savedPath = `${path}/${fileName}`
      alert('已保存到 ' + savedPath)
      showToast('已保存 ' + fileName)
    } catch (e) {
      alert('保存失败：' + (e && e.message ? e.message : String(e)))
    }
  }

  // ─── 导入 Excel/CSV → 回放页 ───
  const handleImportClick = () => {
    if (importInputRef.current) importInputRef.current.value = ''
    importInputRef.current && importInputRef.current.click()
  }

  const handleImportFile = async (e) => {
    const file = e.target.files && e.target.files[0]
    if (!file) return
    pushLog(`导入文件：${file.name}`, 'info')
    try {
      const buf = await file.arrayBuffer()
      const frames = parseWorkbookArrayBuffer(buf)
      pushLog(`解析成功：${frames.length} 帧，进入回放页`, 'success')
      setReplayFrames(frames, { name: name || '', fileName: file.name })
      navigate('/replay')
    } catch (err) {
      pushLog('导入失败：' + (err && err.message ? err.message : String(err)), 'error')
      alert('导入失败：' + (err && err.message ? err.message : String(err)))
    }
  }

  // ─── 生成报告 ───
  const handleGenerateReport = async () => {
    const api = window.electronAPI
    if (!api) {
      alert('请在桌面应用内使用')
      return
    }
    // 检查每块是否有数据
    for (const n of PADS) {
      if (!framesRef.current[n] || framesRef.current[n].length === 0) {
        alert(`第${n}块无数据`)
        return
      }
    }
    const csv = {
      1: buildCsv(framesRef.current[1]),
      2: buildCsv(framesRef.current[2]),
      3: buildCsv(framesRef.current[3]),
      4: buildCsv(framesRef.current[4]),
    }
    const weightNum = weight.trim() === '' ? '' : Number(weight)
    setReportLoading(true)
    pushLog(`点击生成报告（采集数据，姓名=${name || '未填'}）`, 'info')
    try {
      const res = await api.generateReport({
        csv,
        name: name || '',
        weight: weightNum,
      })
      if (res && res.ok) {
        pushLog('报告已生成并打开', 'success')
      } else {
        pushLog('报告生成失败：' + ((res && res.error) || '未知错误'), 'error')
        alert((res && res.error) || '报告生成失败')
      }
    } catch (e) {
      pushLog('报告生成异常：' + (e && e.message ? e.message : String(e)), 'error')
      alert('报告生成失败：' + (e && e.message ? e.message : String(e)))
    } finally {
      setReportLoading(false)
    }
  }

  // ─── 导入 CSV 生成报告（无需先采集）───
  const handleImportReport = async () => {
    if (!window.electronAPI) {
      alert('请在桌面应用内使用')
      return
    }
    try {
      const { canceled, path } = await window.electronAPI.selectImportDirectory()
      if (canceled) return
      const weightNum = weight.trim() === '' ? '' : Number(weight)
      setReportLoading(true)
      const res = await window.electronAPI.generateReportFromDir({
        dir: path,
        name: name || '',
        weight: weightNum,
      })
      if (res && res.ok) {
        alert('报告已生成并打开')
      } else {
        alert((res && res.error) || '报告生成失败')
      }
    } catch (e) {
      alert('报告生成失败：' + (e && e.message ? e.message : String(e)))
    } finally {
      setReportLoading(false)
    }
  }

  // 综合连接状态
  const connState = wsConnected ? 'online' : 'connecting'
  const connText = wsConnected
    ? deviceReady || !window.electronAPI
      ? '已连接'
      : '已连接(设备未就绪)'
    : '连接中/断开'

  const totalFrames = frameCount[1] + frameCount[2] + frameCount[3] + frameCount[4]

  return (
    <div className="capture-wrap">
      {/* 顶部条 */}
      <div className="cap-topbar">
        <div className="cap-top-left">
          <button className="cap-back" onClick={() => navigate('/')}>
            ← 返回
          </button>
          <span className="cap-title">足底压力采集</span>
        </div>

        <div className="cap-top-center">
          <div className="cap-conn">
            <span className={`conn-dot ${connState}`} />
            <span>{connText}</span>
          </div>
          <div className="cap-tiles">
            {PADS.map((n) => (
              <div className="tile-ind" key={n}>
                <span className={`tile-dot ${padOnline[n] ? 'on' : ''}`} />
                <span>{n}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="cap-top-right">
          <label className="cap-field">
            姓名
            <input
              className="wk-input"
              type="text"
              placeholder="可空"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="cap-field">
            体重
            <input
              className="wk-input small"
              type="number"
              placeholder="kg"
              value={weight}
              onChange={(e) => setWeight(e.target.value)}
            />
          </label>
        </div>
      </div>

      {/* 主区：一整条步道点云（默认）或 2x2 热力图 */}
      <div className="cap-main">
        {viewMode === 'walkway' ? (
          <div className="gait-stage">
            <GaitWalkway
              sensorData={{
                sensor1: matrices[1],
                sensor2: matrices[2],
                sensor3: matrices[3],
                sensor4: matrices[4],
              }}
            />
          </div>
        ) : (
          <div className="heat-grid">
            {PADS.map((n) => (
              <HeatmapGrid key={n} matrix={matrices[n]} label={`${n} 号垫`} />
            ))}
          </div>
        )}
      </div>

      {/* 底部操作条 */}
      <div className="cap-bottombar">
        {!isRecording ? (
          <button className="wk-btn wk-btn-primary" onClick={handleStart}>
            开始采集
          </button>
        ) : (
          <button className="wk-btn wk-btn-danger" onClick={handleStop}>
            停止采集
          </button>
        )}

        <div className="cap-stats">
          <span>
            计时 <span className="cap-stat-val">{elapsed}</span> s
          </span>
          <span>
            已采集 <span className="cap-stat-val">{totalFrames}</span> 帧
          </span>
        </div>

        <div className="cap-actions">
          <button
            className="wk-btn"
            onClick={() => setViewMode((m) => (m === 'walkway' ? 'heatmap' : 'walkway'))}
          >
            {viewMode === 'walkway' ? '切换热力图' : '切换点云步道'}
          </button>
          <button className="wk-btn" onClick={handleImportClick}>
            导入数据(Excel/CSV)
          </button>
          <button
            className="wk-btn"
            onClick={handleImportReport}
            disabled={reportLoading}
          >
            {reportLoading ? '处理中…' : '导入CSV生成报告'}
          </button>
          {hasStopped && (
            <>
              <button
                className="wk-btn"
                onClick={handleSaveXlsx}
                disabled={isRecording}
              >
                保存
              </button>
              <button
                className="wk-btn wk-btn-primary"
                onClick={handleGenerateReport}
                disabled={isRecording || reportLoading}
              >
                {reportLoading ? '生成中…' : '生成报告'}
              </button>
            </>
          )}
        </div>
      </div>

      <div style={{ padding: '0 16px 16px' }}>
        <LogConsole height={140} />
      </div>

      {/* 隐藏的导入文件输入（Excel/CSV → 回放页） */}
      <input
        ref={importInputRef}
        type="file"
        accept=".xlsx,.xls,.csv"
        style={{ display: 'none' }}
        onChange={handleImportFile}
      />

      {toast && <div className="wk-toast">{toast}</div>}
    </div>
  )
}
