import assert from 'node:assert/strict'
import test from 'node:test'
import { createFootprintTracker, footprintColor, FOOTPRINT_COLOR_GRADIENT, FOOT_COLS, FOOT_ROWS, FOOT_RENDER_WIDTH, FOOT_RENDER_HEIGHT, FOOT_RENDER_SCALE, smoothFootprint, paintFootprint } from './footprintPreview.js'
import { DEFAULT_RENDER_SETTINGS, pressureOpacity } from './renderSettings.js'

// 与报告一致的俯视坐标造数据，再还原成前端传感器矩阵。
function sensors(rectangles) {
  const data = Object.fromEntries([1, 2, 3, 4].map((n) => [`sensor${n}`, Array.from({ length: 64 }, () => Array(64).fill(0))]))
  for (const { x, y, width = 7, height = 17, value = 120 } of rectangles) {
    for (let row = y; row < y + height; row++) {
      for (let col = x; col < x + width; col++) {
        data[`sensor${4 - Math.floor(row / 64)}`][row % 64][63 - col] = value
      }
    }
  }
  return data
}
const sum = (a) => a.reduce((total, value) => total + value, 0)

test('左右脚分离并使用相同压力单位，保留原始矩阵', () => {
  const data = sensors([{ x: 12, y: 20, value: 60 }, { x: 43, y: 90, value: 210 }])
  const original = structuredClone(data)
  const result = createFootprintTracker().update(data)
  assert.ok(result.left.active && result.right.active)
  assert.equal(result.left.peak, 60)
  assert.equal(result.right.peak, 210)
  assert.deepEqual(data, original)
})

test('跨越脚垫接缝仍是同一个完整脚印', () => {
  const tracker = createFootprintTracker()
  const within = tracker.update(sensors([{ x: 12, y: 20 }]))
  const across = tracker.update(sensors([{ x: 12, y: 58 }]))
  assert.deepEqual(across.left.values, within.left.values)
  assert.equal(sum(across.right.values), 0)
})

test('行走位置平移后脚印仍停在固定位置', () => {
  const tracker = createFootprintTracker()
  const first = tracker.update(sensors([{ x: 12, y: 50 }]))
  const next = tracker.update(sensors([{ x: 16, y: 180 }]))
  assert.deepEqual(first.left.values, next.left.values)
})

test('接触区扩大时采样点间距不变，不按接触范围缩放', () => {
  const tracker = createFootprintTracker()
  const markers = [{ x: 15, y: 24, width: 1, height: 1, value: 240 }, { x: 15, y: 32, width: 1, height: 1, value: 250 }]
  const small = tracker.update(sensors([{ x: 12, y: 20, height: 17, value: 40 }, ...markers])).left
  const large = tracker.update(sensors([{ x: 12, y: 2, height: 55, value: 40 }, ...markers])).left
  const distance = (foot) => (foot.values.indexOf(250) - foot.values.indexOf(240)) / FOOT_COLS
  assert.equal(distance(small), 8)
  assert.equal(distance(large), 8)
  assert.equal(sum(large.values), 7 * 55 * 40 - 2 * 40 + 240 + 250)
})

test('抬脚和空帧不会保留旧脚印，也不自动补另一只脚', () => {
  const tracker = createFootprintTracker()
  tracker.update(sensors([{ x: 12, y: 20 }, { x: 43, y: 90 }]))
  const single = tracker.update(sensors([{ x: 12, y: 20 }]))
  assert.ok(single.left.active && !single.right.active)
  assert.equal(sum(single.right.values), 0)
  const empty = tracker.update({})
  assert.ok(!empty.left.active && !empty.right.active)
  assert.equal(sum(empty.left.values) + sum(empty.right.values), 0)
})

test('脚弓断开的前掌与足跟合并，阈值过滤和孤立噪点不产生假脚印', () => {
  const tracker = createFootprintTracker()
  const separated = tracker.update(sensors([{ x: 12, y: 20, height: 6 }, { x: 12, y: 31, height: 6 }]))
  assert.equal(sum(separated.left.values), 7 * 12 * 120)
  const filtered = tracker.update(sensors([{ x: 12, y: 20, value: 3 }, { x: 50, y: 90, width: 1, height: 1 }]), 4)
  assert.ok(!filtered.left.active && !filtered.right.active)
})

test('两脚偏离步道中心时，根据横向间距分离；复位后不沿用旧中线', () => {
  const tracker = createFootprintTracker()
  const shifted = tracker.update(sensors([{ x: 5, y: 20 }, { x: 22, y: 90 }]))
  assert.ok(shifted.left.active && shifted.right.active)
  tracker.reset()
  const single = tracker.update(sensors([{ x: 22, y: 90 }]))
  assert.ok(single.left.active && !single.right.active)
})

test('自定义热力色阶保留非等距节点和末端粉色区间', () => {
  const colors = [[0, '#E7EDF3'], [0.16, '#D6E9FA'], [0.27, '#A8CEFF'], [0.44, '#78A9F2'], [0.56, '#61CCAE'], [0.67, '#ECDB5B'], [0.78, '#FFAD33'], [0.86, '#FD6363'], [0.97, '#FF3396'], [1, '#FF3396']]
  for (const [position, hex] of colors) {
    const rgb = [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16))
    assert.deepEqual(footprintColor(position * 255), rgb)
    assert.ok(FOOTPRINT_COLOR_GRADIENT.includes(`${hex} ${Math.round(position * 100)}%`))
  }
  assert.deepEqual(footprintColor(0.50 * 255), [109, 187, 208])
  assert.deepEqual(footprintColor(0.99 * 255), [255, 51, 150])
  assert.deepEqual(footprintColor(-10), footprintColor(0))
  assert.deepEqual(footprintColor(512), footprintColor(255))
  assert.notDeepEqual(footprintColor(60), footprintColor(210))
  assert.deepEqual(footprintColor(0.25 * 255, 255, 0.5), footprintColor(0.5 * 255))
})

test('小窗平滑生成柔和边缘并保留内部压力平台', () => {
  const source = new Float32Array(FOOT_COLS * FOOT_ROWS)
  for (let y = 10; y < 30; y++) for (let x = 8; x < 22; x++) source[y * FOOT_COLS + x] = 200
  const target = new Float32Array(source.length)
  smoothFootprint(source, target)
  assert.equal(target[20 * FOOT_COLS + 15], 200)
  assert.ok(target[10 * FOOT_COLS + 8] > 0 && target[10 * FOOT_COLS + 8] < 200)
  assert.ok(Math.abs(sum(source) - sum(target)) < sum(source) * 1e-6)
})

test('空间平滑控制轮廓柔和度，保持对称和原始压力数据', () => {
  const source = new Float32Array(FOOT_COLS * FOOT_ROWS)
  const center = 32 * FOOT_COLS + 24
  source[center] = 200
  const original = source.slice()
  const soft = new Float32Array(source.length), sharp = new Float32Array(source.length)
  smoothFootprint(source, sharp, 0.4)
  smoothFootprint(source, soft, 1.2)
  assert.ok(soft[center] < sharp[center])
  assert.ok(soft[center + 2] > 0)
  assert.equal(soft[center - 2], soft[center + 2])
  assert.equal(soft[center - 2 * FOOT_COLS], soft[center + 2 * FOOT_COLS])
  assert.ok(Math.abs(sum(soft) - 200) < 1e-4)
  assert.deepEqual(source, original)
  smoothFootprint(source, sharp, 0)
  assert.deepEqual(sharp, source)
})

test('细分后按插值压力映射配色和透明度，空帧清除所有旧像素', () => {
  const field = new Float32Array(FOOT_COLS * FOOT_ROWS)
  for (let row = 0; row < FOOT_ROWS; row++) {
    field[row * FOOT_COLS + 8] = 80
    field[row * FOOT_COLS + 9] = 240
  }
  const original = field.slice()
  const pixels = new Uint8ClampedArray(FOOT_RENDER_WIDTH * FOOT_RENDER_HEIGHT * 4)
  const settings = { ...DEFAULT_RENDER_SETTINGS, colorCurve: 1, edgeFade: 160 }
  paintFootprint(field, pixels, 255, settings)
  // 采样位置 x=8.125，压力应为 100，而不是先给两个端点上色再混色。
  const offset = (20 * FOOT_RENDER_SCALE * FOOT_RENDER_WIDTH + 8 * FOOT_RENDER_SCALE + 2) * 4
  const expected = footprintColor(100)
  for (let i = 0; i < 3; i++) assert.ok(Math.abs(pixels[offset + i] - expected[i]) <= 1)
  assert.equal(pixels[offset + 3], Math.round(255 * pressureOpacity(100, 160)))
  assert.deepEqual(field, original)
  field.fill(0)
  paintFootprint(field, pixels)
  assert.ok(pixels.every((value) => value === 0))
})
