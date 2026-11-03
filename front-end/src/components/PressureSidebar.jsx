import { memo } from 'react'
import { PRESSURE_HISTORY_LENGTH, PRESSURE_SENSORS } from '../lib/pressureTelemetry.js'
import { useLivePressureTelemetry, useReplayPressureTelemetry } from '../lib/usePressureTelemetry.js'
import './PressureSidebar.css'

const numberFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 })
const axisFormat = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 })
const GAIT_METRICS = [
  { label: '步频', unit: 'steps/min' },
  { label: '步幅', unit: 'cm' },
  { label: '速度', unit: 'm/s' },
  { label: '对称性', unit: '' },
]

function PressureCurve({ history }) {
  let peak = 0
  let hasData = false
  for (const sample of history) {
    for (const value of sample) {
      if (value !== null) hasData = true
      if (value > peak) peak = value
    }
  }
  const ceiling = Math.max(peak * 1.1, 1)
  const left = 32
  const right = 278
  const top = 8
  const bottom = 90
  const pathFor = (sensorIndex) => {
    let connected = false
    return history.map((sample, i) => {
      const value = sample[sensorIndex]
      if (value === null) {
        connected = false
        return ''
      }
      const x = left + ((PRESSURE_HISTORY_LENGTH - history.length + i) / (PRESSURE_HISTORY_LENGTH - 1)) * (right - left)
      const y = bottom - (value / ceiling) * (bottom - top)
      const point = `${connected ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`
      connected = true
      // 单帧也显示一个短线段，缺帧不连线。
      return `${point}h0.1`
    }).join(' ')
  }

  return (
    <div className="pressure-plot">
      <svg viewBox="0 0 284 102" preserveAspectRatio="none" role="img" aria-label="四个传感器的总压力曲线，最近 120 次采样，单位 ADC">
        {[0, 0.5, 1].map((fraction) => (
          <g key={fraction}>
            <line className="pressure-plot-grid" x1={left} x2={right} y1={bottom - fraction * (bottom - top)} y2={bottom - fraction * (bottom - top)} />
            <text className="pressure-plot-tick" x={left - 5} y={bottom - fraction * (bottom - top) + 3} textAnchor="end">
              {peak ? axisFormat.format(ceiling * fraction) : fraction === 0 ? '0' : ''}
            </text>
          </g>
        ))}
        {PRESSURE_SENSORS.map(({ id, color }, index) => (
          <path key={id} d={pathFor(index)} fill="none" stroke={color} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
      {!hasData && <span className="pressure-plot-empty">等待压力数据</span>}
    </div>
  )
}

function PressureSidebar({ stats, history, replay = false }) {
  return (
    <aside className={`pressure-sidebar${replay ? ' pressure-sidebar-replay' : ''}`} aria-label={replay ? '回放压力数据' : '实时压力数据'}>
      <div className="pressure-sidebar-stack">
        <section className="pressure-glass pressure-curve-card" aria-label="实时压力曲线">
          <header className="pressure-card-head">
            <h2><i className="pressure-dot" />实时压力曲线</h2>
            <span className="pressure-card-meta">{replay ? '回放' : '实时'} · ADC</span>
          </header>
          <PressureCurve history={history} />
          <div className="pressure-legend">
            {PRESSURE_SENSORS.map(({ id, color }) => (
              <span key={id} style={{ '--sensor-color': color }}><i />传感器{id}</span>
            ))}
          </div>
        </section>

        <div className="pressure-sensor-grid">
          {PRESSURE_SENSORS.map(({ id, color }, index) => {
            const stat = stats[index]
            return (
              <section key={id} className="pressure-glass pressure-sensor-card" style={{ '--sensor-color': color }} aria-label={`传感器${id}统计`}>
                <header className="pressure-card-head">
                  <h2><i className="pressure-dot" />传感器{id}</h2>
                  <span className="pressure-sensor-state">{stat.available ? replay ? '回放' : '在线' : replay ? '无数据' : '未接入'}</span>
                </header>
                <dl className="pressure-readings">
                  <div><dt>总压力</dt><dd>{numberFormat.format(stat.total)}</dd></div>
                  <div><dt>最大值</dt><dd>{numberFormat.format(stat.max)}</dd></div>
                  <div><dt>有效点</dt><dd>{numberFormat.format(stat.active)}</dd></div>
                </dl>
              </section>
            )
          })}
        </div>

        <section className="pressure-glass pressure-gait-card" aria-label="综合指标">
          <header className="pressure-card-head">
            <h2><i className="pressure-dot" />综合指标</h2>
            <span className="pressure-card-meta">待分析</span>
          </header>
          <dl className="pressure-readings">
            {GAIT_METRICS.map(({ label, unit }) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd><span aria-label="尚未计算">—</span>{unit && <span className="pressure-unit">{unit}</span>}</dd>
              </div>
            ))}
          </dl>
          <p className="pressure-gait-note">完整步态指标见分析报告</p>
        </section>
      </div>
    </aside>
  )
}

export const LivePressureSidebar = memo(function LivePressureSidebar({ sessionKey }) {
  const telemetry = useLivePressureTelemetry(sessionKey)
  return <PressureSidebar {...telemetry} />
})

export function ReplayPressureSidebar({ frames, index }) {
  const telemetry = useReplayPressureTelemetry(frames, index)
  return <PressureSidebar {...telemetry} replay />
}
