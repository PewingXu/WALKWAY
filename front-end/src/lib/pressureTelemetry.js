export const PRESSURE_SENSORS = [
  { id: 1, color: '#2176c7' },
  { id: 2, color: '#16865b' },
  { id: 3, color: '#9954bd' },
  { id: 4, color: '#c27621' },
]

export const PRESSURE_HISTORY_LENGTH = 120

// 原始 ADC 统计，不将未标定的读数换算为 N / kPa。
// 有效点 = 有限且大于 0 的原始单元，与显示用的插值、色阶和翻转无关。
export function summarizePressure(values) {
  let total = 0
  let max = 0
  let active = 0
  const available = (Array.isArray(values) || ArrayBuffer.isView(values)) && values.length > 0
  if (available) {
    for (const value of values) {
      if (!Number.isFinite(value) || value <= 0) continue
      total += value
      if (value > max) max = value
      active++
    }
  }
  return { total, max, active, available }
}

export function emptyPressureStats() {
  return PRESSURE_SENSORS.map(() => summarizePressure(null))
}
