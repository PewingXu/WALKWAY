import { useEffect, useMemo, useState } from 'react'
import { backendBridge } from './BackendBridge.js'
import { emptyPressureStats, PRESSURE_HISTORY_LENGTH, PRESSURE_SENSORS, summarizePressure } from './pressureTelemetry.js'

const SAMPLE_INTERVAL_MS = 100
const STALE_AFTER_MS = 1500

// 独立以 10 Hz 更新读数，避免高频串口帧引起整页额外重绘。
export function useLivePressureTelemetry(sessionKey) {
  const [telemetry, setTelemetry] = useState(() => ({ stats: emptyPressureStats(), history: [] }))

  useEffect(() => {
    const latest = new Map()
    let history = []
    const clear = () => {
      latest.clear()
      history = []
      setTelemetry({ stats: emptyPressureStats(), history })
    }
    clear()
    const offs = PRESSURE_SENSORS.map(({ id }) => backendBridge.on(`foot${id}Data`, (values) => {
      latest.set(id, { values, receivedAt: performance.now() })
    }))
    offs.push(backendBridge.on('disconnect', clear))
    offs.push(backendBridge.on('deviceStatus', ({ type, status }) => {
      if (status !== 'online') latest.delete(Number(type.replace('foot', '')))
    }))

    const timer = window.setInterval(() => {
      const now = performance.now()
      const stats = PRESSURE_SENSORS.map(({ id }) => {
        const entry = latest.get(id)
        return summarizePressure(entry && now - entry.receivedAt <= STALE_AFTER_MS ? entry.values : null)
      })
      history = [...history.slice(-(PRESSURE_HISTORY_LENGTH - 1)), stats.map((s) => s.available ? s.total : null)]
      setTelemetry({ stats, history })
    }, SAMPLE_INTERVAL_MS)

    return () => {
      window.clearInterval(timer)
      offs.forEach((off) => off())
    }
  }, [sessionKey])

  return telemetry
}

// 缓存每一帧的小型统计结果；拖动进度条时按实际帧重建窗口，不追加重复采样。
export function useReplayPressureTelemetry(frames, index) {
  const cache = useMemo(() => new WeakMap(), [frames])
  return useMemo(() => {
    if (!frames?.length) return { stats: emptyPressureStats(), history: [] }
    const end = Math.min(Math.max(index, 0), frames.length - 1)
    const history = []
    let stats
    for (let i = Math.max(0, end - PRESSURE_HISTORY_LENGTH + 1); i <= end; i++) {
      const frame = frames[i]
      stats = cache.get(frame)
      if (!stats) {
        stats = PRESSURE_SENSORS.map(({ id }) => summarizePressure(frame.tiles?.[id]))
        cache.set(frame, stats)
      }
      history.push(stats.map((s) => s.available ? s.total : null))
    }
    return { stats, history }
  }, [cache, frames, index])
}
