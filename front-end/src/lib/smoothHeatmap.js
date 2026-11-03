import { jet } from './three-util.js'

export const HEATMAP_ROWS = 64
export const HEATMAP_COLS = 256
export const HEATMAP_SCALE = 4
export const HEATMAP_WIDTH = HEATMAP_COLS * HEATMAP_SCALE
export const HEATMAP_HEIGHT = HEATMAP_ROWS * HEATMAP_SCALE

// 在压力值上做轻量高斯平滑，再插值、着色；避免直接模糊 RGB 导致色阶失真。
const SIGMA = 1.0
const RADIUS = 3
const kernel = Array.from({ length: RADIUS * 2 + 1 }, (_, i) => {
  const x = i - RADIUS
  return Math.exp(-(x * x) / (2 * SIGMA * SIGMA))
})
const kernelSum = kernel.reduce((sum, value) => sum + value, 0)
kernel.forEach((value, i) => { kernel[i] = value / kernelSum })
const palette = Array.from({ length: 1024 }, (_, i) => jet(0, 1023, i))

export function createHeatmapBuffers() {
  return {
    horizontal: new Float32Array(HEATMAP_ROWS * HEATMAP_COLS),
    smooth: new Float32Array(HEATMAP_ROWS * HEATMAP_COLS),
  }
}

export function paintSmoothHeatmap(source, pixels, buffers, colorMax = 0) {
  const { horizontal, smooth } = buffers
  for (let row = 0; row < HEATMAP_ROWS; row++) {
    for (let col = 0; col < HEATMAP_COLS; col++) {
      let value = 0
      for (let k = -RADIUS; k <= RADIUS; k++) {
        const c = col + k
        if (c >= 0 && c < HEATMAP_COLS) value += source[row * HEATMAP_COLS + c] * kernel[k + RADIUS]
      }
      horizontal[row * HEATMAP_COLS + col] = value
    }
  }
  let peak = 0
  for (let row = 0; row < HEATMAP_ROWS; row++) {
    for (let col = 0; col < HEATMAP_COLS; col++) {
      let value = 0
      for (let k = -RADIUS; k <= RADIUS; k++) {
        const r = row + k
        if (r >= 0 && r < HEATMAP_ROWS) value += horizontal[r * HEATMAP_COLS + col] * kernel[k + RADIUS]
      }
      smooth[row * HEATMAP_COLS + col] = value
      if (value > peak) peak = value
    }
  }

  pixels.fill(0)
  const ceiling = colorMax > 0 ? colorMax : peak
  if (peak <= 0 || ceiling <= 0) return
  const fade = Math.max(0.15, ceiling * 0.04)
  for (let y = 0; y < HEATMAP_HEIGHT; y++) {
    // 对齐单元中心；翻转前后保持完全对称，不产生半个像素的位置偏差。
    const sy = (y + 0.5) / HEATMAP_SCALE - 0.5
    const row = Math.floor(sy)
    const dy = sy - row
    const r0 = Math.max(0, row) * HEATMAP_COLS
    const r1 = Math.min(HEATMAP_ROWS - 1, row + 1) * HEATMAP_COLS
    for (let x = 0; x < HEATMAP_WIDTH; x++) {
      const sx = (x + 0.5) / HEATMAP_SCALE - 0.5
      const col = Math.floor(sx)
      const dx = sx - col
      const c0 = Math.max(0, col)
      const c1 = Math.min(HEATMAP_COLS - 1, col + 1)
      const upper = smooth[r0 + c0] * (1 - dx) + smooth[r0 + c1] * dx
      const lower = smooth[r1 + c0] * (1 - dx) + smooth[r1 + c1] * dx
      const value = upper * (1 - dy) + lower * dy
      if (value <= 0) continue
      const color = palette[Math.min(1023, Math.round(value / ceiling * 1023))]
      const offset = (y * HEATMAP_WIDTH + x) * 4
      pixels[offset] = color[0]
      pixels[offset + 1] = color[1]
      pixels[offset + 2] = color[2]
      // 扩大边缘羽化带，使用 smoothstep 让轮廓两端的透明度变化也连续。
      const opacity = Math.min(1, value / fade)
      pixels[offset + 3] = Math.round(255 * opacity * opacity * (3 - 2 * opacity))
    }
  }
}
