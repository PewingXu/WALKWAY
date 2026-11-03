import { useEffect, useRef, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { backendBridge } from '../lib/BackendBridge.js'
import { buildCsvRow, buildCsv } from '../lib/csvExport.js'
import { buildWalkwayXlsxBase64, fileStampFromTs } from '../lib/xlsxExport.js'
import { parseWorkbookArrayBuffer } from '../lib/dataImport.js'
import { setReplayFrames } from '../lib/replayStore.js'
import WalkwayFlatHeatmap from '../components/WalkwayFlatHeatmap.jsx'
import GaitWalkway from '../components/GaitWalkway.jsx'
import LogConsole from '../components/LogConsole.jsx'
import BrandHeader from '../components/BrandHeader.jsx'
import DeviceDialog from '../components/DeviceDialog.jsx'
import RenderParams from '../components/RenderParams.jsx'
import AdcTrendChart from '../components/AdcTrendChart.jsx'
import { pushLog } from '../lib/logBus.js'

const PADS = [1, 2, 3, 4]
// ADC 走势曲线的时间窗（采样点数；设备约 40~77Hz，120 点 ≈ 2~3 秒）
const ADC_WINDOW = 120
// 四条曲线的配色，与脚垫编号一一对应
const ADC_COLORS = ['#1A4FA0', '#2B7FD4', '#16A394', '#7CC44A']

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
  // 显示左右翻转（点云 + 热力图同时反转走行方向）
  const [flip, setFlip] = useState(false)
  // 「连接与设置」弹窗
  const [deviceDialogOpen, setDeviceDialogOpen] = useState(false)

  // ─── 渲染参数（色阶上限 / 过滤阈值）───
  const [autoColor, setAutoColor] = useState(true)
  const [colorMax, setColorMax] = useState(300)
  const [threshold, setThreshold] = useState(1)

  // ─── 各块 ADC 走势（每块一条曲线，窗口内滚动）───
  const [adcSeries, setAdcSeries] = useState({ 1: [], 2: [], 3: [], 4: [] })

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

  // 姓名与系统密钥解耦：密钥仅用于进入系统，不再预填姓名。姓名由操作者手动填写。

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

        // ADC 走势：取该帧峰值（最能反映踩踏强度），滚动保留最近 ADC_WINDOW 个采样
        let peak = 0
        for (let i = 0; i < arr.length; i++) if (arr[i] > peak) peak = arr[i]
        setAdcSeries((prev) => {
          const next = prev[n].length >= ADC_WINDOW
            ? prev[n].slice(prev[n].length - ADC_WINDOW + 1)
            : prev[n].slice()
          next.push(peak)
          return { ...prev, [n]: next }
        })

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

  // ─── 一键连接：立即强制重连设备（不必等 3 秒自动重连）───
  const handleConnect = () => {
    backendBridge.connect()
    showToast('正在连接设备…')
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
  // 注意：wsConnected 只代表「前端↔本地后端服务」的 WS 通了（服务常驻，始终为真），
  // 真正的「设备是否连接」要看脚垫在线数（padOnline）。
  const onlineCount = PADS.reduce((acc, n) => acc + (padOnline[n] ? 1 : 0), 0)
  const deviceConnected = onlineCount > 0
  const connState = !wsConnected ? 'offline' : deviceConnected ? 'online' : 'connecting'
  const connText = !wsConnected
    ? '服务未连接'
    : deviceConnected
      ? `设备已连接 (${onlineCount}/4)`
      : '等待设备接入…'

  const totalFrames = frameCount[1] + frameCount[2] + frameCount[3] + frameCount[4]

  // 当前帧峰值：取四块最新采样的最大值。
  // 不用热力图回调，否则切到 3D 点云时这个读数会停在 0。
  const frameMax = PADS.reduce((acc, n) => {
    const s = adcSeries[n]
    const v = s.length ? s[s.length - 1] : 0
    return v > acc ? v : acc
  }, 0)

  return (
    <div className="capture-wrap">
      {/* 顶部条 */}
      <div className="cap-topbar">
        <div className="cap-top-left">
          <BrandHeader title="足底压力采集" subtitle="Foot Pressure Walkway" />
        </div>

        <div className="cap-top-right">
          {/* 常驻的连接态指示：一眼能看出设备通没通，详情进弹窗 */}
          <div className="cap-conn">
            <span className={`conn-dot ${connState}`} />
            <span>{connText}</span>
          </div>
          <button
            className={`wk-btn cap-connect-btn${deviceConnected ? ' is-connected' : ''}`}
            onClick={() => setDeviceDialogOpen(true)}
            title="设备连接状态与受试者信息"
          >
            连接与设置
          </button>
        </div>
      </div>

      <DeviceDialog
        open={deviceDialogOpen}
        onClose={() => setDeviceDialogOpen(false)}
        onReconnect={handleConnect}
        connState={connState}
        connText={connText}
        onlineCount={onlineCount}
        padOnline={padOnline}
        name={name}
        onNameChange={setName}
        weight={weight}
        onWeightChange={setWeight}
      />

      {/* 主区：舞台标题行 + 一整条步道点云（默认）或平面热力图 */}
      <div className="cap-main">
        <div className="cap-stage-head">
          <span className="cap-stage-title">
            {viewMode === 'walkway' ? '步道点云' : '压力热力图'}
            <span className="muted">
              {viewMode === 'walkway' ? '3D 空间视图' : '四块拼接 · 64 × 256'}
            </span>
          </span>
          <div className="cap-stage-tools">
            <div className="wk-seg">
              <button
                className={`wk-seg-btn${viewMode === 'walkway' ? ' active' : ''}`}
                onClick={() => setViewMode('walkway')}
              >
                3D 点云
              </button>
              <button
                className={`wk-seg-btn${viewMode === 'heatmap' ? ' active' : ''}`}
                onClick={() => setViewMode('heatmap')}
              >
                平面热力图
              </button>
            </div>
            <button
              className={`wk-btn wk-btn-sm${flip ? ' wk-btn-primary' : ''}`}
              onClick={() => setFlip((f) => !f)}
              title="左右翻转点云 / 热力图显示（反转走行方向）"
            >
              {flip ? '已翻转' : '左右翻转'}
            </button>
          </div>
        </div>

        {/* 舞台：画面铺满，渲染参数与四块 ADC 走势作为浮层叠在视图内 */}
        <div className={`gait-stage${viewMode === 'heatmap' ? ' heat-flat-stage' : ''}`}>
          {viewMode === 'walkway' ? (
            <GaitWalkway
              sensorData={{
                sensor1: matrices[1],
                sensor2: matrices[2],
                sensor3: matrices[3],
                sensor4: matrices[4],
              }}
              flip={flip}
              particleParams={{
                colorRange: autoColor ? 700 : colorMax,
                filterThreshold: threshold,
              }}
            />
          ) : (
            <WalkwayFlatHeatmap
              sensorData={{
                sensor1: matrices[1],
                sensor2: matrices[2],
                sensor3: matrices[3],
                sensor4: matrices[4],
              }}
              flip={flip}
              colorMax={autoColor ? 0 : colorMax}
              threshold={threshold}
            />
          )}

          {/* 浮层 1：渲染参数（视图右上角） */}
          <div className="stage-overlay stage-overlay-tr">
            <RenderParams
              auto={autoColor}
              onAutoChange={setAutoColor}
              colorMax={colorMax}
              onColorMaxChange={setColorMax}
              threshold={threshold}
              onThresholdChange={setThreshold}
              frameMax={frameMax}
            />
          </div>

          {/* 浮层 2：四块 ADC 波动（视图底部横向铺开，与步道四块左右对应） */}
          <div className="adc-overlay">
            <div className="adc-overlay-head">
              <span className="adc-group-title">ADC 波动</span>
              <span className="adc-group-sub">各垫峰值 · 最近 {ADC_WINDOW} 帧</span>
            </div>
            <div className="adc-overlay-row">
              {PADS.map((n) => (
                <AdcTrendChart
                  key={n}
                  label={`${n} 号垫`}
                  points={adcSeries[n]}
                  online={!!padOnline[n]}
                  color={ADC_COLORS[n - 1]}
                  peak={autoColor ? 0 : colorMax}
                />
              ))}
            </div>
          </div>
        </div>
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

        {/* 视图切换与翻转已移至主区标题行（cap-stage-head） */}
        <div className="cap-actions">
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
