// 只计算画布内的初始展示区域；玻璃浮层和舞台本身仍保持原位置、原尺寸。
export function getWalkwayViewport(container) {
  const stage = container.closest('.gait-stage') || container
  const width = container.clientWidth
  const height = container.clientHeight
  const sidebar = stage.querySelector('.pressure-sidebar')
  const params = stage.querySelector('.stage-overlay-tr')
  const adc = stage.querySelector('.adc-overlay')
  const gap = 20
  const left = sidebar ? sidebar.offsetLeft + sidebar.offsetWidth + gap : gap
  const top = params ? params.offsetTop + params.offsetHeight + gap : gap
  const right = width - gap
  const bottom = adc ? adc.offsetTop - gap : height - gap
  return {
    width: Math.max(1, right - left),
    height: Math.max(1, bottom - top),
    centerX: (left + right) / 2,
    centerY: (top + bottom) / 2,
  }
}

export function observeWalkwayViewport(container, onResize) {
  const observer = new ResizeObserver(onResize)
  const stage = container.closest('.gait-stage') || container
  observer.observe(container)
  for (const overlay of stage.querySelectorAll('.pressure-sidebar, .stage-overlay-tr, .adc-overlay')) {
    observer.observe(overlay)
  }
  return () => observer.disconnect()
}
