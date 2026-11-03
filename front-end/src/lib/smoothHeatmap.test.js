import assert from 'node:assert/strict'
import test from 'node:test'
import { createHeatmapBuffers, HEATMAP_COLS, HEATMAP_ROWS, HEATMAP_WIDTH, HEATMAP_HEIGHT, paintSmoothHeatmap, smoothPressureField } from './smoothHeatmap.js'

function render(source, colorMax = 0, pixels = new Uint8ClampedArray(HEATMAP_WIDTH * HEATMAP_HEIGHT * 4)) {
  paintSmoothHeatmap(source, pixels, createHeatmapBuffers(), colorMax)
  return pixels
}

test('稀疏压力点生成连续透明边缘，并保持原始压力数据不变', () => {
  const source = new Float32Array(HEATMAP_ROWS * HEATMAP_COLS)
  source[32 * HEATMAP_COLS + 80] = 140
  const original = source.slice()
  const pixels = render(source)
  const alphas = pixels.filter((_, i) => i % 4 === 3)
  assert.deepEqual(source, original)
  assert.ok(alphas.some((alpha) => alpha > 0 && alpha < 255))
  assert.ok(alphas.some((alpha) => alpha === 255))
  assert.equal(pixels[3], 0, '远离脚印的零压区保持透明')
  assert.ok(alphas.filter((alpha) => alpha > 0).length > 16, '不再是一个放大的原始方格')
})

test('零帧清除上一帧，不残留脚印', () => {
  const source = new Float32Array(HEATMAP_ROWS * HEATMAP_COLS)
  source[100] = 255
  const pixels = render(source)
  source.fill(0)
  render(source, 300, pixels)
  assert.ok(pixels.every((value) => value === 0))
})

test('平滑和插值在 180 度翻转后保持脚印位置与颜色对称', () => {
  const source = new Float32Array(HEATMAP_ROWS * HEATMAP_COLS)
  source[2 * HEATMAP_COLS + 254] = 120
  source[31 * HEATMAP_COLS + 63] = 200
  const normal = render(source)
  const rotated = render(source.slice().reverse())
  for (let i = 0; i < HEATMAP_WIDTH * HEATMAP_HEIGHT; i++) {
    const opposite = HEATMAP_WIDTH * HEATMAP_HEIGHT - i - 1
    for (let channel = 0; channel < 4; channel++) {
      assert.ok(Math.abs(normal[i * 4 + channel] - rotated[opposite * 4 + channel]) <= 1)
    }
  }
})

test('手动色阶不会按每一帧的峰值重新归一化', () => {
  const low = new Float32Array(HEATMAP_ROWS * HEATMAP_COLS).fill(30)
  const high = new Float32Array(HEATMAP_ROWS * HEATMAP_COLS).fill(120)
  const center = (HEATMAP_WIDTH * 128 + 512) * 4
  const lowPixels = render(low, 255)
  const highPixels = render(high, 255)
  assert.notDeepEqual(lowPixels.slice(center, center + 3), highPixels.slice(center, center + 3))
})

test('空间平滑为零时保留原压力场，增大后压力扩散但总量不变', () => {
  const source = new Float32Array(HEATMAP_ROWS * HEATMAP_COLS)
  const center = 32 * HEATMAP_COLS + 128
  source[center] = 255
  const buffers = createHeatmapBuffers()
  smoothPressureField(source, buffers, 0)
  assert.deepEqual(buffers.smooth, source)
  smoothPressureField(source, buffers, 1.2)
  const smallerPeak = buffers.smooth[center]
  smoothPressureField(source, buffers, 3)
  assert.ok(buffers.smooth[center] < smallerPeak)
  assert.ok(Math.abs(buffers.smooth.reduce((a, b) => a + b, 0) - 255) < 0.001)
  assert.equal(source[center], 255)
})
