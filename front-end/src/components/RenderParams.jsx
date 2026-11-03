import { useId } from 'react'
import { DEFAULT_RENDER_SETTINGS, RENDER_SLIDERS } from '../lib/renderSettings.js'
import './PressureSidebar.css'
import './RenderParams.css'

export default function RenderParams({ settings, onChange, frameMax = 0 }) {
  const id = useId()
  const fittedColorMax = Math.max(1, Math.min(255, Math.ceil(frameMax)))
  const setValue = (key, value) => onChange((previous) => ({ ...previous, [key]: value }))
  const renderSlider = ({ key, label, min, max, step, hint }) => (
    <label className={`rp-row${key === 'colorMax' && settings.autoColor ? ' is-disabled' : ''}`} key={key} title={hint}>
      <span className="rp-label">{label}</span>
      <input
        className="rp-range"
        type="range"
        min={min}
        max={max}
        step={step}
        value={settings[key]}
        disabled={key === 'colorMax' && settings.autoColor}
        onChange={(event) => setValue(key, Number(event.target.value))}
      />
      <output className="rp-val">{key === 'colorMax' && settings.autoColor ? Math.round(frameMax) : settings[key]}</output>
    </label>
  )

  return (
    <details className="rp-panel pressure-glass" onKeyDown={(event) => {
      if (event.key === 'Escape') {
        event.currentTarget.open = false
        event.currentTarget.querySelector('summary').focus()
      }
    }}>
      <summary className="rp-toggle" aria-controls={`${id}-body`}>
        <span className="rp-title"><span aria-hidden="true">⚙</span> 渲染参数</span>
        <svg className="rp-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
      </summary>
      <div className="rp-body" id={`${id}-body`}>
        <div className="rp-auto-row">
          <label className="rp-check">
            <input type="checkbox" checked={settings.autoColor} onChange={(event) => setValue('autoColor', event.target.checked)} />
            自动色阶
          </label>
          <span className="rp-ramp" title="低压 → 高压">蓝 → 青 → 绿 → 黄 → 红</span>
        </div>
        {RENDER_SLIDERS.slice(0, 3).map(renderSlider)}
        <div className="rp-actions">
          <button type="button" disabled={frameMax <= 0} onClick={() => onChange((previous) => ({ ...previous, autoColor: false, colorMax: fittedColorMax }))} title={frameMax > 0 ? `将颜色上限设为 ${fittedColorMax}` : '当前没有压力数据'}>
            <span aria-hidden="true">↗</span> 适配当前峰值
          </button>
          <button type="button" onClick={() => onChange({ ...DEFAULT_RENDER_SETTINGS })} title="恢复全部渲染参数的默认值">
            <span aria-hidden="true">↺</span> 复位
          </button>
        </div>
        <div className="rp-smoothing">{RENDER_SLIDERS.slice(3).map(renderSlider)}</div>
      </div>
    </details>
  )
}
