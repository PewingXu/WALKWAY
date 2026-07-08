import { useRef, useEffect } from 'react'

/**
 * HeatmapGrid - 64x64 压力热力图（canvas 渲染 + CSS 放大）
 *
 * props:
 *  - matrix: 64x64 二维数组（已转置），可能为 null
 *  - label:  标注文本（如 "1 号垫"）
 *
 * 实现：用 ImageData 一次性绘制 64x64 像素，再由 CSS 放大到显示尺寸
 * （image-rendering: pixelated）。使用 jet/turbo 风格配色。
 */

// turbo/jet 风格配色：t ∈ [0,1] -> [r,g,b]
function turbo(t) {
  if (t < 0) t = 0
  if (t > 1) t = 1
  // 近似 turbo colormap 的分段配色（蓝->青->绿->黄->红）
  const r = Math.round(
    34.61 +
      t * (1172.33 - t * (10793.56 - t * (33300.12 - t * (38394.49 - t * 14825.05))))
  )
  const g = Math.round(
    23.31 +
      t * (557.33 + t * (1225.33 - t * (3574.96 - t * (1073.77 + t * 707.56))))
  )
  const b = Math.round(
    27.2 +
      t * (3211.1 - t * (15327.97 - t * (27814.0 - t * (22569.18 - t * 6838.66))))
  )
  return [clamp8(r), clamp8(g), clamp8(b)]
}

function clamp8(v) {
  if (v < 0) return 0
  if (v > 255) return 255
  return v
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
