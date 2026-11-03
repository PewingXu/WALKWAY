import { DEFAULT_RENDER_SETTINGS, pressureColor, pressureOpacity } from './renderSettings.js'

export const HEATMAP_ROWS = 64
export const HEATMAP_COLS = 256
export const HEATMAP_SCALE = 4
export const HEATMAP_WIDTH = HEATMAP_COLS * HEATMAP_SCALE
export const HEATMAP_HEIGHT = HEATMAP_ROWS * HEATMAP_SCALE

// 在压力值上做轻量高斯平滑，再插值、着色；避免直接模糊 RGB 导致色阶失真。
const kernels = new Map()
function gaussianKernel(sigma) {
  if (!kernels.has(sigma)) {
    const radius = Math.ceil(sigma * 3)
    const kernel = Array.from({ length: radius * 2 + 1 }, (_, i) => Math.exp(-((i - radius) ** 2) / (2 * sigma * sigma)))
    const sum = kernel.reduce((total, value) => total + value, 0)
    kernels.set(sigma, kernel.map((value) => value / sum))
  }
  return kernels.get(sigma)
}

export function createHeatmapBuffers() {
  return {
    horizontal: new Float32Array(HEATMAP_ROWS * HEATMAP_COLS),
    smooth: new Float32Array(HEATMAP_ROWS * HEATMAP_COLS),
    display: new Float32Array(HEATMAP_ROWS * HEATMAP_COLS),
  }
}

export function smoothPressureField(source, buffers, sigma = DEFAULT_RENDER_SETTINGS.spatialSmooth) {
  const { horizontal, smooth } = buffers
  if (sigma <= 0) {
    smooth.set(source)
    return
  }
  const kernel = gaussianKernel(sigma)
  const radius = (kernel.length - 1) / 2
  for (let row = 0; row < HEATMAP_ROWS; row++) {
    for (let col = 0; col < HEATMAP_COLS; col++) {
      let value = 0
      for (let k = -radius; k <= radius; k++) {
        const c = col + k
        if (c >= 0 && c < HEATMAP_COLS) value += source[row * HEATMAP_COLS + c] * kernel[k + radius]
      }
      horizontal[row * HEATMAP_COLS + col] = value
    }
  }
  for (let row = 0; row < HEATMAP_ROWS; row++) {
    for (let col = 0; col < HEATMAP_COLS; col++) {
      let value = 0
      for (let k = -radius; k <= radius; k++) {
        const r = row + k
        if (r >= 0 && r < HEATMAP_ROWS) value += horizontal[r * HEATMAP_COLS + col] * kernel[k + radius]
      }
      smooth[row * HEATMAP_COLS + col] = value
    }
  }

}

export function paintPressureField(smooth, pixels, colorMax = 0, settings = DEFAULT_RENDER_SETTINGS) {
  let peak = 0
  for (const value of smooth) if (value > peak) peak = value
  pixels.fill(0)
  const ceiling = colorMax > 0 ? colorMax : peak
  if (peak <= 0 || ceiling <= 0) return
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
      const color = pressureColor(value, ceiling, settings.colorCurve)
      const offset = (y * HEATMAP_WIDTH + x) * 4
      pixels[offset] = color[0]
      pixels[offset + 1] = color[1]
      pixels[offset + 2] = color[2]
      pixels[offset + 3] = Math.round(255 * pressureOpacity(value, settings.edgeFade))
    }
  }
}

export function paintSmoothHeatmap(source, pixels, buffers, colorMax = 0, settings = DEFAULT_RENDER_SETTINGS) {
  smoothPressureField(source, buffers, settings.spatialSmooth)
  paintPressureField(buffers.smooth, pixels, colorMax, settings)
}
