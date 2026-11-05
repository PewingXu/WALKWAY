import { useEffect, useRef, useState } from 'react'
import { createFootprintTracker, FOOT_COLS, FOOT_ROWS, FOOT_RENDER_WIDTH, FOOT_RENDER_HEIGHT, FOOTPRINT_COLOR_GRADIENT, paintFootprint, smoothFootprint } from '../lib/footprintPreview.js'
import { DEFAULT_RENDER_SETTINGS, transitionFactor } from '../lib/renderSettings.js'
import './PressureSidebar.css'
import './FootprintPreview.css'

const SENSOR_KEYS = ['sensor1', 'sensor2', 'sensor3', 'sensor4']
const STALE_MS = 1500
const DISPLAY_SCALE = 5

export default function FootprintPreview({ sensorData, settings = DEFAULT_RENDER_SETTINGS, live = false, online, resetKey = 0 }) {
  const canvasRef = useRef(null)
  const propsRef = useRef(null)
  const [active, setActive] = useState([false, false])
  propsRef.current = { sensorData, settings, live, online, resetKey }

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')
    const tracker = createFootprintTracker()
    const sources = SENSOR_KEYS.map(() => ({ matrix: null, receivedAt: 0 }))
    const layers = [0, 1].map(() => {
      const layer = document.createElement('canvas')
      layer.width = FOOT_RENDER_WIDTH; layer.height = FOOT_RENDER_HEIGHT
      const context = layer.getContext('2d')
      return { layer, context, image: context.createImageData(FOOT_RENDER_WIDTH, FOOT_RENDER_HEIGHT), horizontal: new Float32Array(FOOT_COLS * FOOT_ROWS), target: new Float32Array(FOOT_COLS * FOOT_ROWS), display: new Float32Array(FOOT_COLS * FOOT_ROWS) }
    })
    let animationId, lastTime = 0, lastSettings, lastReset, lastAvailable = []
    let contacts = tracker.update({}), activeKey = '', dirty = true, ceiling = settings.colorMax
    const draw = (now) => {
      animationId = requestAnimationFrame(draw)
      const frameInterval = 1000 / (propsRef.current.live ? 30 : 60)
      if (now - lastTime < frameInterval - 0.5) return
      const elapsed = lastTime ? now - lastTime : frameInterval
      lastTime = now
      const props = propsRef.current
      const reset = lastReset !== props.resetKey
      const changedSettings = lastSettings !== props.settings
      const available = {}
      let changed = reset
      SENSOR_KEYS.forEach((key, index) => {
        const matrix = props.sensorData?.[key] || null
        if (sources[index].matrix !== matrix) sources[index] = { matrix, receivedAt: now }
        const connected = !props.live || props.online?.[index + 1] !== false
        available[key] = connected && (!props.live || now - sources[index].receivedAt <= STALE_MS) ? matrix : null
        if (lastAvailable[index] !== available[key]) changed = true
        lastAvailable[index] = available[key]
      })
      if (reset) tracker.reset()
      if (changed || lastSettings?.threshold !== props.settings.threshold || lastSettings?.spatialSmooth !== props.settings.spatialSmooth) {
        contacts = tracker.update(available, props.settings.threshold)
        const key = `${contacts.left.active}:${contacts.right.active}`
        if (key !== activeKey) { activeKey = key; setActive([contacts.left.active, contacts.right.active]) }
        for (const [index, foot] of [contacts.left, contacts.right].entries()) {
          smoothFootprint(foot.values, layers[index].target, props.settings.spatialSmooth, layers[index].horizontal)
          if (reset || !foot.active) layers[index].display.set(layers[index].target)
        }
        dirty = true
      }
      lastSettings = props.settings; lastReset = props.resetKey
      if (changedSettings) dirty = true
      if (!dirty) return
      const peak = Math.max(contacts.left.peak, contacts.right.peak)
      ceiling = props.settings.autoColor ? peak || ceiling : props.settings.colorMax
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      const factor = transitionFactor(props.settings.transitionSmooth, elapsed)
      let settling = false
      layers.forEach((layer, index) => {
        for (let i = 0; i < layer.display.length; i++) {
          const difference = layer.target[i] - layer.display[i]
          if (Math.abs(difference) < 0.02 || factor === 1) layer.display[i] = layer.target[i]
          else { layer.display[i] += difference * factor; settling = true }
        }
        paintFootprint(layer.display, layer.image.data, ceiling, props.settings)
        layer.context.putImageData(layer.image, 0, 0)
        const width = FOOT_COLS * DISPLAY_SCALE, height = FOOT_ROWS * DISPLAY_SCALE
        const slotWidth = canvas.width / 2
        const slotX = index * slotWidth // 左脚在左，右脚在右。
        ctx.imageSmoothingEnabled = true
        ctx.imageSmoothingQuality = 'high'
        ctx.save()
        ctx.beginPath()
        ctx.rect(slotX + 4, 4, slotWidth - 8, canvas.height - 8)
        ctx.clip()
        // 保留俯视采样坐标，脚尖朝上；只平移，不旋转或按接触范围缩放。
        ctx.translate(slotX + slotWidth / 2, canvas.height / 2)
        ctx.save()
        ctx.globalAlpha = 0.25
        ctx.filter = 'blur(2.5px)'
        ctx.drawImage(layer.layer, -width / 2, -height / 2, width, height)
        ctx.restore()
        ctx.drawImage(layer.layer, -width / 2, -height / 2, width, height)
        ctx.restore()
      })
      dirty = settling
    }
    animationId = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(animationId)
  }, [])

  return (
    <section className="footprint-preview pressure-glass" aria-label={live ? '实时左右脚脚印' : '回放左右脚脚印'}>
      <header><span className="footprint-preview-dot" />{live ? '实时脚印' : '回放脚印'}<span className="footprint-preview-mode">原地</span></header>
      <div className="footprint-preview-field">
        <canvas ref={canvasRef} width={288} height={320} aria-label="左脚在左、右脚在右，脚尖朝上；颜色表示压力大小" />
        <div className="footprint-preview-labels">
          {[{ label: '左脚', index: 0 }, { label: '右脚', index: 1 }].map(({ label, index }) => <span key={label} className={active[index] ? 'is-active' : ''}><i />{label}</span>)}
        </div>
      </div>
      <div className="footprint-preview-scale"><span>低</span><i style={{ background: FOOTPRINT_COLOR_GRADIENT }} /><span>高</span></div>
    </section>
  )
}
