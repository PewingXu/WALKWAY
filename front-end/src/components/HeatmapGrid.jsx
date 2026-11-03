import { useRef, useEffect } from 'react'
import { jet } from '../lib/three-util'

/**
 * HeatmapGrid - 64x64 压力热力图（canvas 渲染 + CSS 放大）
 *
 * props:
 *  - matrix: 64x64 二维数组（已转置），可能为 null
 *  - label:  标注文本（如 "1 号垫"）
 *
 * 实现：用 ImageData 一次性绘制 64x64 像素，再由 CSS 放大到显示尺寸
 * （image-rendering: pixelated）。配色统一走 three-util 的 jet 色阶。
 */

// t ∈ [0,1] -> [r,g,b]，复用全站统一色阶
function turbo(t) {
  return jet(0, 1, t)
}

const SIZE = 64

export default function HeatmapGrid({ matrix, label }) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    if (!matrix) {
      ctx.clearRect(0, 0, SIZE, SIZE)
      return
    }

    // 求当前帧最大值用于归一化
    let max = 0
    for (let r = 0; r < SIZE; r++) {
      const row = matrix[r]
      if (!row) continue
      for (let c = 0; c < SIZE; c++) {
        const v = row[c]
        if (v > max) max = v
      }
    }
    if (max <= 0) max = 1

    const img = ctx.createImageData(SIZE, SIZE)
    const data = img.data
    for (let r = 0; r < SIZE; r++) {
      const row = matrix[r] || []
      for (let c = 0; c < SIZE; c++) {
        const v = row[c] || 0
        const t = v / max
        const [cr, cg, cb] = turbo(t)
        const idx = (r * SIZE + c) * 4
        data[idx] = cr
        data[idx + 1] = cg
        data[idx + 2] = cb
        data[idx + 3] = 255
      }
    }
    ctx.putImageData(img, 0, 0)
  }, [matrix])

  return (
    <div className="heat-cell">
      <div className="heat-cell-label">{label}</div>
      <div className="heat-canvas-box">
        <canvas
          ref={canvasRef}
          className="heat-canvas"
          width={SIZE}
          height={SIZE}
        />
        {!matrix && <div className="heat-empty-txt">暂无数据</div>}
      </div>
    </div>
  )
}
