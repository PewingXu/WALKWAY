import { DEFAULT_RENDER_SETTINGS, pressureOpacity } from './renderSettings.js'

// 将整条步道的当前接触区归到左右两个固定位置；不累积历史压力，也不修改采集数据。
export const FOOT_COLS = 48
export const FOOT_ROWS = 64
export const FOOT_RENDER_SCALE = 4
export const FOOT_RENDER_WIDTH = FOOT_COLS * FOOT_RENDER_SCALE
export const FOOT_RENDER_HEIGHT = FOOT_ROWS * FOOT_RENDER_SCALE
const WIDTH = 64
const HEIGHT = 256
const SIZE = WIDTH * HEIGHT
const KEYS = ['sensor1', 'sensor2', 'sensor3', 'sensor4']

export function createFootprintTracker() {
  const field = new Float32Array(SIZE)
  const visited = new Uint8Array(SIZE)
  const queue = new Int32Array(SIZE)
  let midline = 31.5
  let previous = [null, null]

  return {
    reset() { midline = 31.5; previous = [null, null] },
    update(sensorData, threshold = 2) {
      field.fill(0)
      // 与报告的镜像俯视坐标一致：foot1 在下方，foot4 在上方，横轴左小右大。
      for (let s = 0; s < KEYS.length; s++) {
        const matrix = sensorData[KEYS[s]]
        if (!matrix) continue
        for (let row = 0; row < 64; row++) {
          for (let col = 0; col < 64; col++) {
            const value = matrix[row]?.[col]
            if (Number.isFinite(value) && value > 0 && value >= threshold) field[((3 - s) * 64 + row) * WIDTH + 63 - col] = value
          }
        }
      }
      visited.fill(0)
      const regions = []
      for (let index = 0; index < SIZE; index++) {
        if (!field[index] || visited[index]) continue
        let head = 0, tail = 1
        queue[0] = index
        visited[index] = 1
        const region = { points: [], minX: WIDTH, maxX: 0, minY: HEIGHT, maxY: 0, total: 0, sumX: 0, sumY: 0 }
        while (head < tail) {
          const point = queue[head++]
          const x = point % WIDTH, y = Math.floor(point / WIDTH), value = field[point]
          region.points.push(point)
          region.minX = Math.min(region.minX, x); region.maxX = Math.max(region.maxX, x)
          region.minY = Math.min(region.minY, y); region.maxY = Math.max(region.maxY, y)
          region.total += value; region.sumX += x * value; region.sumY += y * value
          // 跨过一格坏线，四块传感器接缝处也按一个接触区处理。
          for (let dy = -2; dy <= 2; dy++) {
            for (let dx = -2; dx <= 2; dx++) {
              const nx = x + dx, ny = y + dy
              if (nx < 0 || nx >= WIDTH || ny < 0 || ny >= HEIGHT) continue
              const next = ny * WIDTH + nx
              if (field[next] && !visited[next]) { visited[next] = 1; queue[tail++] = next }
            }
          }
        }
        if (region.points.length >= 3) regions.push(region)
      }
      // 脚弓可能不接触地面；合并邻近的前掌、足跟和脚趾，而非把它们当成不同脚。
      regions.sort((a, b) => b.total - a.total)
      const merged = []
      for (const region of regions.slice(0, 64)) {
        let target = region
        for (let i = merged.length - 1; i >= 0; i--) {
          const other = merged[i]
          const gapX = Math.max(0, target.minX - other.maxX - 1, other.minX - target.maxX - 1)
          const gapY = Math.max(0, target.minY - other.maxY - 1, other.minY - target.maxY - 1)
          const width = Math.max(target.maxX, other.maxX) - Math.min(target.minX, other.minX)
          const height = Math.max(target.maxY, other.maxY) - Math.min(target.minY, other.minY)
          if (gapX > 2 || gapY > 7 || width > 25 || height > 42) continue
          target = {
            points: [...target.points, ...other.points], total: target.total + other.total,
            sumX: target.sumX + other.sumX, sumY: target.sumY + other.sumY,
            minX: Math.min(target.minX, other.minX), maxX: Math.max(target.maxX, other.maxX),
            minY: Math.min(target.minY, other.minY), maxY: Math.max(target.maxY, other.maxY),
          }
          merged.splice(i, 1)
        }
        merged.push(target)
      }
      const contacts = merged.filter((r) => r.points.length >= 6 && r.total >= 30 && r.maxX - r.minX < 40 && r.maxY - r.minY < 60)
      for (const r of contacts) { r.x = r.sumX / r.total; r.y = r.sumY / r.total }
      // 两侧同时有接触时校准横向中线，适应行走位置略偏离步道中心。
      const strongest = contacts.slice().sort((a, b) => b.total - a.total)
      const first = strongest[0]
      const second = first && strongest.find((r) => Math.abs(r.x - first.x) >= 9)
      if (second) midline = (first.x + second.x) / 2
      const result = [0, 1].map((side) => {
        const candidates = contacts.filter((r) => (r.x <= midline ? 0 : 1) === side)
        // 保持正在显示的接触区；同侧多次落脚时优先显示当前压力最强的脚印。
        candidates.sort((a, b) => b.total - a.total)
        const last = previous[side]
        const nearby = last && candidates.find((r) => Math.hypot(r.x - last.x, r.y - last.y) < 10)
        const contact = nearby && nearby.total >= (candidates[0]?.total || 0) * 0.5 ? nearby : candidates[0]
        previous[side] = contact ? { x: contact.x, y: contact.y } : null
        const values = new Float32Array(FOOT_COLS * FOOT_ROWS)
        if (!contact) return { values, active: false, peak: 0 }
        // 仅平移到固定坐标；每个传感器点始终对应一格，接触范围变化不缩放脚印。
        const centerX = (contact.minX + contact.maxX) / 2
        const centerY = (contact.minY + contact.maxY) / 2
        let peak = 0
        for (const point of contact.points) {
          const x = Math.round(point % WIDTH - centerX + (FOOT_COLS - 1) / 2)
          const y = Math.round(Math.floor(point / WIDTH) - centerY + (FOOT_ROWS - 1) / 2)
          values[y * FOOT_COLS + x] = Math.max(values[y * FOOT_COLS + x], field[point])
          peak = Math.max(peak, field[point])
        }
        return { values, active: true, peak }
      })
      return { left: result[0], right: result[1] }
    },
  }
}

// 脚印和色条共用非等距色阶节点，两脚使用相同的压力上限。
export const FOOTPRINT_COLOR_STOPS = [
  [0, '#E7EDF3'],
  [0.16, '#D6E9FA'],
  [0.27, '#A8CEFF'],
  [0.44, '#78A9F2'],
  [0.56, '#61CCAE'],
  [0.67, '#ECDB5B'],
  [0.78, '#FFAD33'],
  [0.86, '#FD6363'],
  [0.97, '#FF3396'],
  [1, '#FF3396'],
]
export const FOOTPRINT_COLOR_GRADIENT = `linear-gradient(90deg, ${FOOTPRINT_COLOR_STOPS.map(([position, color]) => `${color} ${Math.round(position * 100)}%`).join(', ')})`
const FOOTPRINT_RGB_STOPS = FOOTPRINT_COLOR_STOPS.map(([position, color]) => [position, [1, 3, 5].map((start) => parseInt(color.slice(start, start + 2), 16))])

export function footprintColor(value, ceiling = 255, curve = 1) {
  const position = Math.pow(Math.max(0, Math.min(1, value / Math.max(1, ceiling))), Math.max(0.1, curve))
  let end = 1
  while (end < FOOTPRINT_RGB_STOPS.length - 1 && position > FOOTPRINT_RGB_STOPS[end][0]) end++
  const [startPosition, startColor] = FOOTPRINT_RGB_STOPS[end - 1]
  const [endPosition, endColor] = FOOTPRINT_RGB_STOPS[end]
  const ratio = (position - startPosition) / (endPosition - startPosition)
  return startColor.map((channel, i) => Math.round(channel + (endColor[i] - channel) * ratio))
}

const smoothingKernels = new Map()
export function smoothFootprint(source, output, sigma = DEFAULT_RENDER_SETTINGS.spatialSmooth, horizontal = new Float32Array(source.length)) {
  if (sigma <= 0) { output.set(source); return }
  if (!smoothingKernels.has(sigma)) {
    const radius = Math.ceil(sigma * 3)
    const weights = Array.from({ length: radius * 2 + 1 }, (_, i) => Math.exp(-((i - radius) ** 2) / (2 * sigma * sigma)))
    const total = weights.reduce((sum, value) => sum + value, 0)
    smoothingKernels.set(sigma, weights.map((value) => value / total))
  }
  const weights = smoothingKernels.get(sigma)
  const radius = (weights.length - 1) / 2
  // 在压力值上做可分离高斯平滑，颜色最后映射，避免直接模糊彩色图像。
  for (let y = 0; y < FOOT_ROWS; y++) {
    for (let x = 0; x < FOOT_COLS; x++) {
      let sum = 0
      for (let dx = -radius; dx <= radius; dx++) {
        const nx = x + dx
        if (nx >= 0 && nx < FOOT_COLS) sum += source[y * FOOT_COLS + nx] * weights[dx + radius]
      }
      horizontal[y * FOOT_COLS + x] = sum
    }
  }
  for (let y = 0; y < FOOT_ROWS; y++) {
    for (let x = 0; x < FOOT_COLS; x++) {
      let sum = 0
      for (let dy = -radius; dy <= radius; dy++) {
        const ny = y + dy
        if (ny >= 0 && ny < FOOT_ROWS) sum += horizontal[ny * FOOT_COLS + x] * weights[dy + radius]
      }
      output[y * FOOT_COLS + x] = sum
    }
  }
}

const footprintPalettes = new Map()
export function paintFootprint(field, pixels, ceiling = 255, settings = DEFAULT_RENDER_SETTINGS) {
  const curve = settings.colorCurve
  if (!footprintPalettes.has(curve)) {
    footprintPalettes.set(curve, Array.from({ length: 1024 }, (_, i) => footprintColor(i / 1023, 1, curve)))
  }
  const palette = footprintPalettes.get(curve)
  const max = Math.max(1, ceiling)
  pixels.fill(0)
  // 在采样点中心之间细分压力场，再应用色阶和边缘透明度，保持固定的传感器比例。
  for (let y = 0; y < FOOT_RENDER_HEIGHT; y++) {
    const sy = (y + 0.5) / FOOT_RENDER_SCALE - 0.5
    const row = Math.floor(sy), dy = sy - row
    const row0 = Math.max(0, row) * FOOT_COLS
    const row1 = Math.min(FOOT_ROWS - 1, row + 1) * FOOT_COLS
    for (let x = 0; x < FOOT_RENDER_WIDTH; x++) {
      const sx = (x + 0.5) / FOOT_RENDER_SCALE - 0.5
      const col = Math.floor(sx), dx = sx - col
      const col0 = Math.max(0, col), col1 = Math.min(FOOT_COLS - 1, col + 1)
      const upper = field[row0 + col0] * (1 - dx) + field[row0 + col1] * dx
      const lower = field[row1 + col0] * (1 - dx) + field[row1 + col1] * dx
      const value = upper * (1 - dy) + lower * dy
      if (value <= 0) continue
      const color = palette[Math.round(Math.min(1, value / max) * 1023)]
      const offset = (y * FOOT_RENDER_WIDTH + x) * 4
      pixels[offset] = color[0]; pixels[offset + 1] = color[1]; pixels[offset + 2] = color[2]
      pixels[offset + 3] = Math.round(255 * pressureOpacity(value, settings.edgeFade))
    }
  }
}
