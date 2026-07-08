import { useEffect, useRef, useState } from 'react'
import { subscribe, clearLog } from '../lib/logBus'

const LEVEL_COLOR = {
  info: 'var(--text-secondary)',
  muted: 'var(--text-muted)',
  success: 'var(--success, #2FBF71)',
  error: 'var(--danger, #E5484D)',
  py: '#8Fc7ff',
  'py-err': '#F0A35A',
  device: '#7FD0A8',
  'device-err': '#F0A35A',
}

function fmtTime(ts) {
  const d = new Date(ts)
  const p = (n, l = 2) => String(n).padStart(l, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`
}

/**
 * 终端式日志面板：显示设备/报告生成/错误的实时日志。
 * props: { height } 可选高度（默认 160px），collapsible。
 */
export default function LogConsole({ height = 160 }) {
  const [lines, setLines] = useState([])
  const [open, setOpen] = useState(true)
  const [autoScroll, setAutoScroll] = useState(true)
  const boxRef = useRef(null)

  useEffect(() => subscribe((buf) => setLines(buf.slice())), [])

  useEffect(() => {
    if (open && autoScroll && boxRef.current) {
      boxRef.current.scrollTop = boxRef.current.scrollHeight
    }
  }, [lines, open, autoScroll])

  return (
    <div className="log-console">
      <div className="log-console-bar">
        <span className="log-console-title">运行日志 / 终端</span>
        <span className="log-console-count">{lines.length} 行</span>
        <div style={{ flex: 1 }} />
        <label className="log-console-chk">
          <input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} />
          自动滚动
        </label>
        <button className="log-console-btn" onClick={() => clearLog()}>清空</button>
        <button className="log-console-btn" onClick={() => setOpen((v) => !v)}>{open ? '收起' : '展开'}</button>
      </div>
      {open && (
        <div className="log-console-body" ref={boxRef} style={{ height }}>
          {lines.length === 0 && <div className="log-line" style={{ color: 'var(--text-muted)' }}>（暂无日志）</div>}
          {lines.map((l, i) => (
            <div className="log-line" key={i}>
              <span className="log-ts">{fmtTime(l.ts)}</span>
              <span style={{ color: LEVEL_COLOR[l.level] || 'var(--text-secondary)' }}>{l.line}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
