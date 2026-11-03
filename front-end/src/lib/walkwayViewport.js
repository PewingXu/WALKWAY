// 只计算画布内的初始展示区域；玻璃浮层和舞台本身仍保持原位置、原尺寸。
export function getWalkwayViewport(container) {
  const stage = container.closest('.gait-stage') || container
  const width = container.clientWidth
  const height = container.clientHeight
  const sidebar = stage.querySelector('.pressure-sidebar')
  const params = stage.querySelector('.stage-overlay-tr')
  const toggle = params?.querySelector('.rp-toggle')
  const gap = 20
  const left = sidebar ? sidebar.offsetLeft + sidebar.offsetWidth + gap : gap
  // 展开参数是浮层操作，不应让用户正在观察的视图突然缩小或移动。
  const top = toggle ? params.offsetTop + toggle.offsetHeight + gap : gap
  const right = width - gap
  const bottom = height - gap
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
  // 参数面板仅改变宽度时，不触发默认视角重新拟合。
  for (const overlay of stage.querySelectorAll('.pressure-sidebar')) {
    observer.observe(overlay)
  }
  return () => observer.disconnect()
}
