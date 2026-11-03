import { useRef, useEffect } from 'react'

/**
 * AdcTrendChart - 单块脚垫的 ADC 波动折线图（canvas 绘制的小型实时曲线）
 *
 * props:
 *   - points: number[]  按时间顺序的采样值（最新在末尾），长度即时间窗
 *   - label:  string     标题（如 "1 号垫"）
 *   - online: boolean    离线时整体置灰
 *   - color:  string     线条颜色
 *   - peak:   number     纵轴上限；<=0 时按窗口内最大值自适应
 */
export default function AdcTrendChart({
  points = [],
  label,
  online = false,
  color = '#2B7FD4',
  peak = 0,
}) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // 按设备像素比绘制，避免曲线发虚
    const dpr = window.devicePixelRatio || 1
    const cssW = canvas.clientWidth || 240
    const cssH = canvas.clientHeight || 56
    if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
      canvas.width = cssW * dpr
      canvas.height = cssH * dpr
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, cssW, cssH)

    const PAD_T = 4
    const PAD_B = 4
    const plotH = cssH - PAD_T - PAD_B

    // 基线
    ctx.strokeStyle = 'rgba(148, 163, 180, 0.35)'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(0, cssH - PAD_B + 0.5)
    ctx.lineTo(cssW, cssH - PAD_B + 0.5)
    ctx.stroke()

    if (!points.length) return

    // 纵轴上限：给定 peak 则固定（便于四块横向对比），否则按窗口最大值自适应
    let top = peak > 0 ? peak : 0
    if (top <= 0) {
      for (const v of points) if (v > top) top = v
    }
    if (top <= 0) top = 1

    const stepX = points.length > 1 ? cssW / (points.length - 1) : cssW
    const yOf = (v) => {
      const t = Math.max(0, Math.min(1, v / top))
      return PAD_T + plotH - t * plotH
    }

    // 面积填充
    ctx.beginPath()
    ctx.moveTo(0, cssH - PAD_B)
    points.forEach((v, i) => ctx.lineTo(i * stepX, yOf(v)))
    ctx.lineTo((points.length - 1) * stepX, cssH - PAD_B)
    ctx.closePath()
    ctx.fillStyle = online ? `${color}1F` : 'rgba(148, 163, 180, 0.12)'
    ctx.fill()

    // 折线
    ctx.beginPath()
    points.forEach((v, i) => {
      const x = i * stepX
      const y = yOf(v)
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.strokeStyle = online ? color : 'rgba(148, 163, 180, 0.55)'
    ctx.lineWidth = 1.5
    ctx.lineJoin = 'round'
    ctx.stroke()

    // 末端当前点
    const lastX = (points.length - 1) * stepX
    const lastY = yOf(points[points.length - 1])
    ctx.beginPath()
    ctx.arc(lastX, lastY, 2.5, 0, Math.PI * 2)
    ctx.fillStyle = online ? color : 'rgba(148, 163, 180, 0.8)'
    ctx.fill()
  }, [points, online, color, peak])

  const current = points.length ? points[points.length - 1] : 0

  return (
    <div className={`adc-card${online ? '' : ' is-off'}`}>
      <div className="adc-card-head">
        <span className="adc-card-label">{label}</span>
        <span className="adc-card-val">{Math.round(current)}</span>
      </div>
      <canvas ref={canvasRef} className="adc-card-canvas" />
    </div>
  )
}
