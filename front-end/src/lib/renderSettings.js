export const DEFAULT_RENDER_SETTINGS = Object.freeze({
  autoColor: false,
  colorMax: 255,
  colorCurve: 0.6,
  edgeFade: 6,
  spatialSmooth: 1.2,
  transitionSmooth: 3,
  threshold: 2,
})

export const RENDER_SLIDERS = [
  { key: 'colorMax', label: '颜色上限', min: 1, max: 255, step: 1, hint: '固定色阶的最高压力值（ADC）' },
  { key: 'colorCurve', label: '色阶曲线', min: 0.1, max: 2, step: 0.1, hint: '数值越小，低压力区域的色彩越明显' },
  { key: 'edgeFade', label: '边缘淡出', min: 0, max: 32, step: 1, hint: '低于此压力值的轮廓逐渐透明（ADC）' },
  { key: 'spatialSmooth', label: '空间平滑', min: 0, max: 3, step: 0.1, hint: '柔化脚印轮廓；0 表示关闭空间平滑' },
  { key: 'transitionSmooth', label: '过渡平滑', min: 1, max: 12, step: 1, hint: '平滑帧间变化；1 表示立即更新' },
  { key: 'threshold', label: '过滤阈值', min: 0, max: 60, step: 1, hint: '过滤低于此值的原始压力噪声（ADC）' },
]

// 3D 和平面视图共用色阶、曲线与透明度，统一使用原始 ADC 单位。
const RAMP = [[37, 85, 158], [38, 169, 190], [52, 179, 132], [255, 205, 40], [225, 61, 52]]
const PALETTE = Array.from({ length: 1024 }, (_, index) => {
  const segment = index / 1023 * (RAMP.length - 1)
  const start = Math.min(RAMP.length - 2, Math.floor(segment))
  const ratio = segment - start
  return RAMP[start].map((value, channel) => Math.round(value + (RAMP[start + 1][channel] - value) * ratio))
})

export function pressureColor(value, ceiling, curve = DEFAULT_RENDER_SETTINGS.colorCurve) {
  const ratio = Math.max(0, Math.min(1, value / Math.max(1, ceiling)))
  return PALETTE[Math.round(Math.pow(ratio, Math.max(0.1, curve)) * 1023)]
}

export function pressureOpacity(value, fade = DEFAULT_RENDER_SETTINGS.edgeFade) {
  if (value <= 0) return 0
  if (fade <= 0) return 1
  const ratio = Math.min(1, value / fade)
  return ratio * ratio * (3 - 2 * ratio)
}

export function transitionFactor(smoothness, elapsedMs) {
  if (smoothness <= 1) return 1
  return 1 - Math.pow(1 - 1 / smoothness, Math.max(0, elapsedMs) / (1000 / 60))
}

export function sensorFramePeak(sensorData) {
  let peak = 0
  for (const matrix of Object.values(sensorData)) {
    if (!matrix) continue
    for (const row of matrix) {
      for (const value of row || []) if (Number.isFinite(value) && value > peak) peak = value
    }
  }
  return peak
}
