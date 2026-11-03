import { useRef, useEffect } from 'react'
import { createHeatmapBuffers, HEATMAP_WIDTH, HEATMAP_HEIGHT, paintSmoothHeatmap } from '../lib/smoothHeatmap.js'
import { getWalkwayViewport, observeWalkwayViewport } from '../lib/walkwayViewport.js'

/**
 * WalkwayFlatHeatmap - 四块脚垫衔接的「平面」脚印压力热力图（2D 俯视）
 *
 * 拼接方式与 GaitWalkway（3D 点云）一致：每块 64×64 顺时针旋转 90° 后
 * 沿长轴拼成 64×256 的整体：
 *   1) 坏线补值，消除四块衔接处缝隙
 *   2) 阈值过滤去噪
 *   3) 压力场高斯平滑、4 倍双线性插值、连续色阶与透明边缘
 *   4) 左键拖动平移，滚轮 / 中键拖动缩放，双击复位
 *
 * props:
 *   - sensorData: { sensor1: 64×64矩阵, sensor2, sensor3, sensor4 }（可能有 null）
 *   - flip: 是否额外旋转 180°（反转走行方向；旋转而非镜像，左右脚保持不变）
 *   - colorMax: 色阶上限；<=0 表示按当前帧最大值自动拉伸
 *   - threshold: 噪声过滤阈值（低于此值的单元置 0）
 */

/* ─── 常量 ─── */
const NX = 64 // 拼接后行数（传感器宽度）→ 画面高度（方格行数）
const NY = 256 // 拼接后列数（4 × 64）→ 画面宽度（方格列数）
const SENSOR_KEYS = ['sensor1', 'sensor2', 'sensor3', 'sensor4']

/* ─── 坏线补值：修复 64×256 矩阵中异常低值的行/列（消除四块衔接缝隙）─── */
function zeroLine64x256(arr, rows, cols) {
  const BAD = 40
  const GOOD = 100

  const rowSums = new Float32Array(rows)
  const colSums = new Float32Array(cols)
  for (let r = 0; r < rows; r++) {
    let total = 0
    for (let c = 0; c < cols; c++) total += arr[r * cols + c]
    rowSums[r] = total
  }
  for (let c = 0; c < cols; c++) {
    let total = 0
    for (let r = 0; r < rows; r++) total += arr[r * cols + c]
    colSums[c] = total
  }

  for (let r = 1; r < rows - 1; r++) {
    if (rowSums[r] >= BAD) continue
    if (rowSums[r - 1] > GOOD && rowSums[r + 1] > GOOD) {
      for (let c = 0; c < cols; c++) {
        arr[r * cols + c] = (arr[(r - 1) * cols + c] + arr[(r + 1) * cols + c]) / 2
      }
    } else if (
      r + 2 < rows &&
      rowSums[r + 1] < BAD &&
      rowSums[r - 1] > GOOD &&
      rowSums[r + 2] > GOOD
    ) {
      for (let c = 0; c < cols; c++) {
        const vPrev = arr[(r - 1) * cols + c]
        const vNext = arr[(r + 2) * cols + c]
        arr[r * cols + c] = (vPrev * 2) / 3 + (vNext * 1) / 3
        arr[(r + 1) * cols + c] = (vPrev * 1) / 3 + (vNext * 2) / 3
      }
      r++
    }
  }

  for (let c = 1; c < cols - 1; c++) {
    if (colSums[c] >= BAD) continue
    if (colSums[c - 1] > GOOD && colSums[c + 1] > GOOD) {
      for (let r = 0; r < rows; r++) {
        arr[r * cols + c] = (arr[r * cols + (c - 1)] + arr[r * cols + (c + 1)]) / 2
      }
    } else if (
      c + 2 < cols &&
      colSums[c + 1] < BAD &&
      colSums[c - 1] > GOOD &&
      colSums[c + 2] > GOOD
    ) {
      for (let r = 0; r < rows; r++) {
        const vPrev = arr[r * cols + (c - 1)]
        const vNext = arr[r * cols + (c + 2)]
        arr[r * cols + c] = (vPrev * 2) / 3 + (vNext * 1) / 3
        arr[r * cols + (c + 1)] = (vPrev * 1) / 3 + (vNext * 2) / 3
      }
      c++
    }
  }
}

export default function WalkwayFlatHeatmap({
  sensorData = {},
  flip = false,
  colorMax = 0,
  threshold = 1,
  resetViewKey = 0,
}) {
  const viewportRef = useRef(null)
  const canvasRef = useRef(null)
  const buffersRef = useRef(null)
  const resetViewRef = useRef(null)

  useEffect(() => {
    const viewport = viewportRef.current
    const canvas = canvasRef.current
    let base = { width: 1, height: 1, centerX: 0, centerY: 0 }
    const view = { x: 0, y: 0, scale: 1 }
    let drag = null
    const applyView = () => {
      canvas.style.transform = `translate3d(${view.x}px, ${view.y}px, 0) scale(${view.scale})`
    }
    const resize = () => {
      const area = getWalkwayViewport(viewport)
      const width = Math.min(area.width, area.height * 4, 1200)
      base = { width, height: width / 4, centerX: area.centerX, centerY: area.centerY }
      canvas.style.width = `${base.width}px`
      canvas.style.height = `${base.height}px`
      canvas.style.left = `${base.centerX - base.width / 2}px`
      canvas.style.top = `${base.centerY - base.height / 2}px`
      applyView()
    }
    const reset = () => {
      Object.assign(view, { x: 0, y: 0, scale: 1 })
      resize()
    }
    resetViewRef.current = reset

    const zoomAt = (factor, clientX, clientY) => {
      const rect = viewport.getBoundingClientRect()
      const x = clientX - rect.left - base.centerX
      const y = clientY - rect.top - base.centerY
      const scale = Math.max(0.25, Math.min(8, view.scale * factor))
      const ratio = scale / view.scale
      view.x = x - (x - view.x) * ratio
      view.y = y - (y - view.y) * ratio
      view.scale = scale
      applyView()
    }
    const onWheel = (event) => {
      event.preventDefault()
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1
      const delta = Math.max(-800, Math.min(800, event.deltaY * unit))
      zoomAt(Math.exp(-delta * 0.0015), event.clientX, event.clientY)
    }
    const onPointerDown = (event) => {
      if (drag || !event.isPrimary || (event.button !== 0 && event.button !== 1)) return
      event.preventDefault()
      viewport.focus({ preventScroll: true })
      drag = { id: event.pointerId, button: event.button, x: event.clientX, y: event.clientY, anchorX: event.clientX, anchorY: event.clientY }
      viewport.setPointerCapture(event.pointerId)
      viewport.classList.add('is-dragging')
    }
    const onPointerMove = (event) => {
      if (!drag || drag.id !== event.pointerId) return
      if (drag.button === 1) {
        zoomAt(Math.exp((drag.y - event.clientY) * 0.01), drag.anchorX, drag.anchorY)
      } else {
        view.x += event.clientX - drag.x
        view.y += event.clientY - drag.y
        applyView()
      }
      drag.x = event.clientX
      drag.y = event.clientY
    }
    const endDrag = (event) => {
      if (!drag || drag.id !== event.pointerId) return
      drag = null
      viewport.classList.remove('is-dragging')
      if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId)
    }
    const onKeyDown = (event) => {
      const rect = viewport.getBoundingClientRect()
      if (event.key === 'Home' || event.key === '0') reset()
      else if (event.key === '+' || event.key === '=') zoomAt(1.2, rect.left + base.centerX, rect.top + base.centerY)
      else if (event.key === '-') zoomAt(1 / 1.2, rect.left + base.centerX, rect.top + base.centerY)
      else if (event.key.startsWith('Arrow')) {
        view.x += event.key === 'ArrowLeft' ? -24 : event.key === 'ArrowRight' ? 24 : 0
        view.y += event.key === 'ArrowUp' ? -24 : event.key === 'ArrowDown' ? 24 : 0
        applyView()
      } else return
      event.preventDefault()
    }
    const listeners = { wheel: onWheel, pointerdown: onPointerDown, pointermove: onPointerMove, pointerup: endDrag, pointercancel: endDrag, lostpointercapture: endDrag, dblclick: reset, keydown: onKeyDown }
    for (const [type, handler] of Object.entries(listeners)) viewport.addEventListener(type, handler, { passive: false })
    const stopObserving = observeWalkwayViewport(viewport, resize)
    resize()
    return () => {
      stopObserving()
      for (const [type, handler] of Object.entries(listeners)) viewport.removeEventListener(type, handler)
      resetViewRef.current = null
    }
  }, [])

  useEffect(() => { resetViewRef.current?.() }, [resetViewKey])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    ctx.clearRect(0, 0, HEATMAP_WIDTH, HEATMAP_HEIGHT)
    if (!buffersRef.current) {
      buffersRef.current = { ...createHeatmapBuffers(), image: ctx.createImageData(HEATMAP_WIDTH, HEATMAP_HEIGHT) }
    }

    // 1) 拼接 4 块为 64×256（每块顺时针旋转 90°：原 (row,col) → 新 (col, 63-row)）
    //    宽度轴镜像修正左右脚，再整体旋转 180°（右=起点=foot1，左=终点=foot4；旋转保持左右脚不变）
    const ndata = new Float32Array(NX * NY)
    let hasData = false
    for (let s = 0; s < 4; s++) {
      const matrix = sensorData[SENSOR_KEYS[s]]
      if (!matrix || !Array.isArray(matrix) || matrix.length === 0) continue
      const colOffset = s * 64
      for (let row = 0; row < 64 && row < matrix.length; row++) {
        const mrow = matrix[row]
        if (!mrow) continue
        for (let col = 0; col < 64 && col < mrow.length; col++) {
          const v = Number.isFinite(mrow[col]) && mrow[col] > 0 ? mrow[col] : 0
          const rr = 63 - col // 宽度轴镜像（修正左右脚）
          const cc = colOffset + (63 - row) // 沿长轴拼接
          // 默认朝向：整体旋转 180°；flip 时再旋转 180°
          let R = 63 - rr
          let C = NY - 1 - cc
          if (flip) {
            R = 63 - R
            C = NY - 1 - C
          }
          ndata[R * NY + C] = v
          if (v > 0) hasData = true
        }
      }
    }

    if (!hasData) return // 无数据：透明，露出画布灰色底

    // 2) 坏线补值
    zeroLine64x256(ndata, NX, NY)

    // 3) 阈值过滤去噪
    for (let i = 0; i < ndata.length; i++) {
      if (ndata[i] < threshold) ndata[i] = 0
    }

    // 只平滑显示副本，采集值、传感器统计和导出的数据均保持原始值。
    const buffers = buffersRef.current
    paintSmoothHeatmap(ndata, buffers.image.data, buffers, colorMax)
    ctx.putImageData(buffers.image, 0, 0)
  }, [sensorData, flip, colorMax, threshold])

  return (
    <div ref={viewportRef} className="walkway-flat" tabIndex={0} role="region" aria-label="平面压力热力图：左键拖动，滚轮或中键缩放，双击或 Home 键复位" title="左键拖动 · 滚轮 / 中键缩放 · 双击复位">
      <canvas ref={canvasRef} className="walkway-flat-canvas" width={HEATMAP_WIDTH} height={HEATMAP_HEIGHT} />
    </div>
  )
}
