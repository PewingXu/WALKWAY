/**
 * RenderParams - 渲染参数面板（色阶上限 / 过滤阈值）
 *
 * 色阶上限决定「多大的 ADC 值算满色（黄）」：
 *   - 自动：每帧按画面内最大值拉伸，细节最丰富，但不同帧之间颜色不可比
 *   - 手动：固定上限，颜色含义稳定，适合对比不同受试者或前后两次采集
 *
 * props: {
 *   auto, onAutoChange, colorMax, onColorMaxChange,
 *   threshold, onThresholdChange, frameMax
 * }
 */
export default function RenderParams({
  auto,
  onAutoChange,
  colorMax,
  onColorMaxChange,
  threshold,
  onThresholdChange,
  frameMax = 0,
}) {
  return (
    <div className="rp-panel">
      <div className="rp-head">
        <span className="rp-title">渲染参数</span>
        <span className="rp-ramp" title="低压 → 高压">
          蓝 → 青 → 绿 → 黄
        </span>
      </div>

      <label className="rp-check">
        <input
          type="checkbox"
          checked={auto}
          onChange={(e) => onAutoChange(e.target.checked)}
        />
        自动色阶
        <span className="rp-check-hint">
          {auto ? `当前帧峰值 ${Math.round(frameMax)}` : '固定上限，跨帧可比'}
        </span>
      </label>

      <div className={`rp-row${auto ? ' is-disabled' : ''}`}>
        <span className="rp-label">颜色上限</span>
        <input
          className="rp-range"
          type="range"
          min={20}
          max={1000}
          step={10}
          value={colorMax}
          disabled={auto}
          onChange={(e) => onColorMaxChange(Number(e.target.value))}
        />
        <span className="rp-val">{colorMax}</span>
      </div>

      <div className="rp-row">
        <span className="rp-label">过滤阈值</span>
        <input
          className="rp-range"
          type="range"
          min={0}
          max={60}
          step={1}
          value={threshold}
          onChange={(e) => onThresholdChange(Number(e.target.value))}
        />
        <span className="rp-val">{threshold}</span>
      </div>
    </div>
  )
}
