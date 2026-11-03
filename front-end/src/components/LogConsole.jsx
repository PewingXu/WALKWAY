import { useEffect, useRef, useState } from 'react'
import { subscribe, clearLog } from '../lib/logBus'

// 亮底终端的日志级别配色：取各色深值，保证在浅灰背景上的对比度
const LEVEL_COLOR = {
  info: 'var(--text-secondary)',
  muted: 'var(--text-muted)',
  success: 'var(--success)',
  error: 'var(--danger)',
  py: '#1A6FC4',
  'py-err': '#C2690F',
  device: '#0F8A7C',
  'device-err': '#C2690F',
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
const OPEN_KEY = 'walkway.logConsoleOpen'

export default function LogConsole({ height = 160 }) {
  const [lines, setLines] = useState([])
  // 折叠状态记忆：默认收起，把纵向空间让给主视图
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(OPEN_KEY) === '1'
    } catch (e) {
      return false
    }
  })
  const [autoScroll, setAutoScroll] = useState(true)
  const boxRef = useRef(null)

  useEffect(() => {
    try {
      localStorage.setItem(OPEN_KEY, open ? '1' : '0')
    } catch (e) {}
  }, [open])

  useEffect(() => subscribe((buf) => setLines(buf.slice())), [])

  useEffect(() => {
    if (open && autoScroll && boxRef.current) {
      boxRef.current.scrollTop = boxRef.current.scrollHeight
    }
  }, [lines, open, autoScroll])

  return (
    <div className={`log-console${open ? '' : ' is-collapsed'}`}>
      {/* 整条标题栏可点击折叠；展开时面板向下展开，收起时只留这一条 */}
      <div
        className="log-console-bar"
        onClick={() => setOpen((v) => !v)}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setOpen((v) => !v)
          }
        }}
      >
        <span className={`log-console-caret${open ? ' is-open' : ''}`} aria-hidden="true">
          ▾
        </span>
        <span className="log-console-title">运行日志 / 终端</span>
        <span className="log-console-count">{lines.length} 行</span>
        <div style={{ flex: 1 }} />
        {open && (
          <>
            <label className="log-console-chk" onClick={(e) => e.stopPropagation()}>
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
              />
              自动滚动
            </label>
            <button
              className="log-console-btn"
              onClick={(e) => {
                e.stopPropagation()
                clearLog()
              }}
            >
              清空
            </button>
          </>
        )}
        <span className="log-console-btn">{open ? '收起' : '展开'}</span>
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
