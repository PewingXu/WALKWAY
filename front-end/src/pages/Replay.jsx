import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import GaitWalkway from '../components/GaitWalkway.jsx'
import WalkwayFlatHeatmap from '../components/WalkwayFlatHeatmap.jsx'
import { getReplayFrames, getReplayMeta } from '../lib/replayStore.js'
import { framesToTemplateCsvs, frameToMatrices, frameTimeStr } from '../lib/dataImport.js'
import LogConsole from '../components/LogConsole.jsx'
import BrandHeader from '../components/BrandHeader.jsx'
import DeviceDialog from '../components/DeviceDialog.jsx'
import { pushLog } from '../lib/logBus.js'

const SPEEDS = [0.5, 1, 2]
const BASE_FPS = 50 // 1x 时的推进帧率（数据约 40~77Hz，近似原速回放）

export default function Replay() {
  const navigate = useNavigate()

  const frames = useMemo(() => getReplayFrames(), [])
  const meta = useMemo(() => getReplayMeta(), [])
  const total = frames ? frames.length : 0

  const [idx, setIdx] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [name, setName] = useState((meta && meta.name) || '')
  const [weight, setWeight] = useState('')
  const [reportLoading, setReportLoading] = useState(false)
  // 主视图模式：'walkway' 一整条步道点云（默认）/ 'heatmap' 平面热力图
  const [viewMode, setViewMode] = useState('walkway')
  // 显示左右翻转（点云 + 热力图同时反转走行方向）
  const [flip, setFlip] = useState(false)
  // 「受试者信息」弹窗
  const [subjectDialogOpen, setSubjectDialogOpen] = useState(false)

  const idxRef = useRef(0)
  idxRef.current = idx

  // 播放循环：playing 时按 BASE_FPS * speed 推进帧索引，到末尾停止
  useEffect(() => {
    if (!playing || total === 0) return
    const stepMs = 1000 / (BASE_FPS * speed)
    const timer = setInterval(() => {
      const next = idxRef.current + 1
      if (next >= total) {
        setIdx(total - 1)
        setPlaying(false)
      } else {
        setIdx(next)
      }
    }, stepMs)
    return () => clearInterval(timer)
  }, [playing, speed, total])

  // 当前帧 → 转置矩阵（供点云回放）
  const sensorData = useMemo(() => {
    if (!frames || total === 0) return {}
    const f = frames[Math.min(idx, total - 1)]
    return frameToMatrices(f)
  }, [frames, idx, total])

  const currentTime = useMemo(() => {
    if (!frames || total === 0) return ''
    return frameTimeStr(frames[Math.min(idx, total - 1)])
  }, [frames, idx, total])

  const handlePlayPause = () => {
    if (total === 0) return
    setPlaying((p) => {
      // 若已在末尾，点播放则从头开始
      if (!p && idxRef.current >= total - 1) setIdx(0)
      return !p
    })
  }

  const handleSeek = (e) => {
    const v = Number(e.target.value)
    setIdx(v)
  }

  const handleGenerateReport = async () => {
    const api = window.electronAPI
    if (!api) {
      alert('请在桌面应用内使用（无法生成报告，回放仍可用）')
      return
    }
    if (!frames || total === 0) {
      alert('无数据')
      return
    }
    setReportLoading(true)
    pushLog(`点击生成报告（共 ${total} 帧，姓名=${name || '未填'}，体重=${weight || '默认'}）`, 'info')
    try {
      const csv = framesToTemplateCsvs(frames)
      const weightNum = weight.trim() === '' ? '' : Number(weight)
      const res = await api.generateReport({ csv, name: name || '', weight: weightNum })
      if (res && res.ok) {
        pushLog('报告已生成并打开', 'success')
      } else {
        pushLog('报告生成失败：' + ((res && res.error) || '未知错误'), 'error')
        alert((res && res.error) || '报告生成失败')
      }
    } catch (err) {
      pushLog('报告生成异常：' + (err && err.message ? err.message : String(err)), 'error')
      alert('报告生成失败：' + (err && err.message ? err.message : String(err)))
    } finally {
      setReportLoading(false)
    }
  }

  // 空数据兜底
  if (!frames || total === 0) {
    return (
      <div className="replay-wrap">
        <div className="replay-empty">
          <p>没有可回放的数据，请先导入 Excel/CSV。</p>
          <button className="wk-btn wk-btn-primary" onClick={() => navigate('/capture')}>
            返回采集页
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="replay-wrap">
      {/* 顶部条 */}
      <div className="cap-topbar">
        <div className="cap-top-left">
          <BrandHeader title="步道数据回放" subtitle="Walkway Replay" />
        </div>

        <div className="cap-top-center">
          {meta && meta.fileName && (
            <span className="replay-filename" title={meta.fileName}>
              {meta.fileName}
            </span>
          )}
        </div>

        <div className="cap-top-right">
          <button className="wk-btn wk-btn-sm" onClick={() => setSubjectDialogOpen(true)}>
            受试者信息
          </button>
          {/* 回放是子页面，保留唯一出口 */}
          <button className="wk-btn wk-btn-sm" onClick={() => navigate('/capture')}>
            返回采集
          </button>
        </div>
      </div>

      <DeviceDialog
        open={subjectDialogOpen}
        onClose={() => setSubjectDialogOpen(false)}
        showDevice={false}
        name={name}
        onNameChange={setName}
        weight={weight}
        onWeightChange={setWeight}
      />

      {/* 主区：舞台标题行 + 一整条步道点云回放，或平面热力图 */}
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

        {viewMode === 'walkway' ? (
          <div className="gait-stage">
            <GaitWalkway sensorData={sensorData} flip={flip} />
          </div>
        ) : (
          <div className="gait-stage heat-flat-stage">
            <WalkwayFlatHeatmap sensorData={sensorData} flip={flip} />
          </div>
        )}
      </div>

      {/* 底部播放控件 */}
      <div className="cap-bottombar replay-bottombar">
        <button className="wk-btn wk-btn-primary" onClick={handlePlayPause}>
          {playing ? '暂停' : '播放'}
        </button>

        <div className="replay-timeline">
          <input
            className="replay-range"
            type="range"
            min={0}
            max={total - 1}
            value={idx}
            onChange={handleSeek}
          />
          <div className="replay-progress-meta">
            <span>
              帧 <span className="cap-stat-val">{idx + 1}</span> / {total}
            </span>
            <span className="replay-time">{currentTime}</span>
          </div>
        </div>

        <div className="replay-speed">
          {SPEEDS.map((s) => (
            <button
              key={s}
              className={`wk-btn wk-speed-btn${speed === s ? ' active' : ''}`}
              onClick={() => setSpeed(s)}
            >
              {s}x
            </button>
          ))}
        </div>

        {/* 视图切换与翻转已移至主区标题行（cap-stage-head） */}
        <div className="cap-actions">
          <button
            className="wk-btn wk-btn-primary"
            onClick={handleGenerateReport}
            disabled={reportLoading}
          >
            {reportLoading ? '生成中…' : '生成报告'}
          </button>
        </div>
      </div>

      <div style={{ padding: '0 16px 16px' }}>
        <LogConsole height={150} />
      </div>
    </div>
  )
}
