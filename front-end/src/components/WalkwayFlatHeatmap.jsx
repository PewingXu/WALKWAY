import { useRef, useEffect } from 'react'
import { jet, findMax } from '../lib/three-util'

/**
 * WalkwayFlatHeatmap - 四块脚垫衔接的「平面」脚印压力热力图（2D 俯视）
 *
 * 拼接方式与 GaitWalkway（3D 点云）一致：每块 64×64 顺时针旋转 90° 后
 * 沿长轴拼成 64×256 的整体，直接按原始单元渲染成方格（不插值、不做高斯平滑）：
 *   1) 坏线补值，消除四块衔接处缝隙
 *   2) 阈值过滤去噪
 *   3) 逐单元 jet 配色，一个传感器单元 = 一个方格；零/无压力单元透明（露出画布灰色底）
 *
 * props:
 *   - sensorData: { sensor1: 64×64矩阵, sensor2, sensor3, sensor4 }（可能有 null）
 *   - flip: 是否额外旋转 180°（反转走行方向；旋转而非镜像，左右脚保持不变）
 */

/* ─── 常量 ─── */
const NX = 64 // 拼接后行数（传感器宽度）→ 画面高度（方格行数）
const NY = 256 // 拼接后列数（4 × 64）→ 画面宽度（方格列数）
const SENSOR_KEYS = ['sensor1', 'sensor2', 'sensor3', 'sensor4']

// 后处理参数
const FILTER_THRESHOLD = 1 // 噪声过滤（低于此值的单元置 0，保留脚跟轻压落地）

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

export default function WalkwayFlatHeatmap({ sensorData = {}, flip = false }) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    ctx.clearRect(0, 0, NY, NX)

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
          const v = mrow[col] || 0
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
      if (ndata[i] < FILTER_THRESHOLD) ndata[i] = 0
    }

    // 4) 逐单元 jet 配色直接渲染方格（一个传感器单元 = 一个像素方格）；零值透明
    //    ndata 索引 i = R*NY + C 与 NY 宽的 ImageData 像素一一对应
    const max = findMax(ndata)
    if (max <= 0) return
    const img = ctx.createImageData(NY, NX)
    const data = img.data
    for (let i = 0; i < ndata.length; i++) {
      const v = ndata[i]
      const idx = i * 4
      if (v <= 0) {
        data[idx + 3] = 0 // 透明
        continue
      }
      const [cr, cg, cb] = jet(0, max, v)
      data[idx] = cr
      data[idx + 1] = cg
      data[idx + 2] = cb
      data[idx + 3] = 255
    }
    ctx.putImageData(img, 0, 0)
  }, [sensorData, flip])

  return (
    <div className="walkway-flat">
      <canvas ref={canvasRef} className="walkway-flat-canvas" width={NY} height={NX} />
    </div>
  )
}
