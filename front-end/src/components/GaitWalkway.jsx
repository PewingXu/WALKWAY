/**
 * GaitWalkway - 基于 Three.js 粒子系统的整条步道压力可视化
 *
 * 移植自参考项目 GaitCanvas.jsx。
 * 4 个 64×64 传感器合并为一个 64×256 的整体粒子系统。
 * 传感器排列：sensor1 | sensor2 | sensor3 | sensor4（沿长轴拼接）
 * 每个 64×64 顺时针旋转 90 度后合并，整体旋转成 3D 步道视角。
 *
 * Props:
 *   - sensorData: { sensor1: 64×64矩阵, ... sensor4: ... }
 *   - particleParams: { gaussSigma, filterThreshold, initValue, colorRange, heightScale }
 *   - transformParams: { scale, particleSize, posX, posY, posZ, rotX, rotY, rotZ }
 *   - onSceneReady: callback
 */
import * as THREE from 'three'
import { TrackballControls } from 'three/examples/jsm/controls/TrackballControls.js'
import { addSide, gaussBlur_1, interp1016, jet } from '../lib/three-util'
import React, { useEffect, useRef } from 'react'

/* ─── 常量 ─── */
const NX = 64 // 合并后行数（传感器宽度）
const NY = 256 // 合并后列数（4 × 64）
const INTERP = 2
const PAD = 4
const AX = NX * INTERP + PAD * 2 // 136
const AY = NY * INTERP + PAD * 2 // 520
const SEP = 80 // 粒子间距
const TOTAL = AX * AY
const SENSOR_KEYS = ['sensor1', 'sensor2', 'sensor3', 'sensor4']

/* ─── 全局纹理缓存 ─── */
let _sharedTexture = null
function getCircleTexture() {
  if (!_sharedTexture) {
    // Electron 使用 app:// 协议加载，必须用相对路径 './circle.png'
    _sharedTexture = new THREE.TextureLoader().load('./circle.png')
  }
  return _sharedTexture
}

export default function GaitWalkway({
  sensorData = {},
  particleParams = {},
  transformParams = {},
  optimizeEnabled = true,
  onSceneReady = null,
}) {
  const containerRef = useRef(null)
  const propsRef = useRef({ sensorData, particleParams, transformParams, optimizeEnabled })
  propsRef.current = { sensorData, particleParams, transformParams, optimizeEnabled }

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    /* ─── 预分配缓冲区 ─── */
    const ndata = new Float32Array(NX * NY)
    const bigArr = new Float32Array(NX * INTERP * NY * INTERP)
    const bigArrg = new Float32Array(AX * AY)
    const smoothBig = new Float32Array(AX * AY)
    const positions = new Float32Array(TOTAL * 3)
    const colors = new Float32Array(TOTAL * 3)
    const scales = new Float32Array(TOTAL)

    // 初始化位置（逆时针旋转 90 度：原 (ix,iy) → 新 X=(AY-1-iy), 新 Z=ix）
    const padWidth = AX * SEP
    const padDepth = AY * SEP
    let i3 = 0,
      i1 = 0
    for (let ix = 0; ix < AX; ix++) {
      for (let iy = 0; iy < AY; iy++) {
        positions[i3] = (AY - 1 - iy) * SEP - padDepth / 2
        positions[i3 + 1] = 0
        positions[i3 + 2] = ix * SEP - padWidth / 2
        scales[i1] = 1
        colors[i3] = 0
        colors[i3 + 1] = 0
        colors[i3 + 2] = 1
        i3 += 3
        i1++
      }
    }

    /* ─── 几何体 & 材质 ─── */
    const geometry = new THREE.BufferGeometry()
    const posAttr = new THREE.BufferAttribute(positions, 3)
    const colAttr = new THREE.BufferAttribute(colors, 3)
    geometry.setAttribute('position', posAttr)
    geometry.setAttribute('color', colAttr)
    geometry.setAttribute('scale', new THREE.BufferAttribute(scales, 1))

    const material = new THREE.PointsMaterial({
      vertexColors: true,
      transparent: true,
      map: getCircleTexture(),
      size: 1,
      depthWrite: false,
      sizeAttenuation: true,
    })

    const particles = new THREE.Points(geometry, material)
    const BASE_SCALE = 0.0062 * 3
    particles.scale.set(BASE_SCALE, BASE_SCALE, BASE_SCALE)
    particles.rotation.x = Math.PI / 3

    const group = new THREE.Group()
    group.add(particles)

    /* ─── 场景 ─── */
    let w = container.clientWidth || window.innerWidth * 0.7
    let h = container.clientHeight || window.innerHeight * 0.8
    if (w < 10) w = window.innerWidth * 0.7
    if (h < 10) h = window.innerHeight * 0.8

    const camera = new THREE.PerspectiveCamera(45, w / h, 1, 200000)
    camera.position.set(0, 600, 800)
    camera.lookAt(0, 0, 0)

    const scene = new THREE.Scene()
    scene.add(group)

    const grid = new THREE.GridHelper(4000, 80)
    grid.position.y = -10
    grid.material.opacity = 0.2
    grid.material.transparent = true
    scene.add(grid)

    scene.add(new THREE.HemisphereLight(0xffffff, 0x444444))
    const dl1 = new THREE.DirectionalLight(0xffffff, 0.8)
    dl1.position.set(0, 400, 200)
    scene.add(dl1)
    const dl2 = new THREE.DirectionalLight(0xffffff, 0.5)
    dl2.position.set(200, 100, 400)
    scene.add(dl2)

    /* ─── 渲染器 ─── */
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(window.devicePixelRatio)
    renderer.setSize(w, h)
    if (renderer.outputColorSpace !== undefined) renderer.outputColorSpace = THREE.SRGBColorSpace
    while (container.firstChild) container.removeChild(container.firstChild)
    container.appendChild(renderer.domElement)
    // 深蓝背景，贴合暗蓝主题
    renderer.setClearColor(0x0a1628)

    /* ─── 控制器 ─── */
    const controls = new TrackballControls(camera, renderer.domElement)
    controls.dynamicDampingFactor = 0.2
    controls.domElement = container
    controls.mouseButtons = {
      LEFT: THREE.MOUSE.PAN,
      MIDDLE: THREE.MOUSE.ZOOM,
      RIGHT: THREE.MOUSE.ROTATE,
    }

    if (onSceneReady) onSceneReady({ scene, camera, renderer })

    /* ─── 坏线补值：检测并修复 64×256 矩阵中异常低值的行/列 ─── */
    function zeroLine64x256(arr, rows, cols) {
      const BAD = 40 // 坏线：行/列总和 < 40
      const GOOD = 100 // 正常：行/列总和 > 100

      // 计算每行和每列的总和
      const rowSums = new Float32Array(rows)
      const colSums = new Float32Array(cols)
      for (let r = 0; r < rows; r++) {
        let total = 0
        for (let c = 0; c < cols; c++) total += arr[r * cols + c]
        rowSums[r] = total
      }
      for (let c = 0; c < cols; c++) {
        let total = 0
        for (let r = 0; r < rows; r++) total += arr[r * cols + c]
        colSums[c] = total
      }

      // --- 修复坏行 ---
      for (let r = 1; r < rows - 1; r++) {
        if (rowSums[r] >= BAD) continue
        // 单行坏线
        if (rowSums[r - 1] > GOOD && rowSums[r + 1] > GOOD) {
          for (let c = 0; c < cols; c++) {
            arr[r * cols + c] = (arr[(r - 1) * cols + c] + arr[(r + 1) * cols + c]) / 2
          }
        }
        // 连续两行坏线
        else if (
          r + 2 < rows &&
          rowSums[r + 1] < BAD &&
          rowSums[r - 1] > GOOD &&
          rowSums[r + 2] > GOOD
        ) {
          for (let c = 0; c < cols; c++) {
            const vPrev = arr[(r - 1) * cols + c]
            const vNext = arr[(r + 2) * cols + c]
            arr[r * cols + c] = (vPrev * 2) / 3 + (vNext * 1) / 3
            arr[(r + 1) * cols + c] = (vPrev * 1) / 3 + (vNext * 2) / 3
          }
          r++
        }
      }

      // --- 修复坏列 ---
      for (let c = 1; c < cols - 1; c++) {
        if (colSums[c] >= BAD) continue
        // 单列坏线
        if (colSums[c - 1] > GOOD && colSums[c + 1] > GOOD) {
          for (let r = 0; r < rows; r++) {
            arr[r * cols + c] = (arr[r * cols + (c - 1)] + arr[r * cols + (c + 1)]) / 2
          }
        }
        // 连续两列坏线
        else if (
          c + 2 < cols &&
          colSums[c + 1] < BAD &&
          colSums[c - 1] > GOOD &&
          colSums[c + 2] > GOOD
        ) {
          for (let r = 0; r < rows; r++) {
            const vPrev = arr[r * cols + (c - 1)]
            const vNext = arr[r * cols + (c + 2)]
            arr[r * cols + c] = (vPrev * 2) / 3 + (vNext * 1) / 3
            arr[r * cols + (c + 1)] = (vPrev * 1) / 3 + (vNext * 2) / 3
          }
          c++
        }
      }
    }

    /* ─── 合并 4 个传感器矩阵为 64×256 flat 数组 ─── */
    // 每个 64×64 传感器顺时针旋转 90 度后再拼接
    // 顺时针 90°：原 (row,col) → 新 (col, 63-row)
    function mergeSensorData() {
      const { sensorData: sd } = propsRef.current
      ndata.fill(0)
      for (let s = 0; s < 4; s++) {
        const key = SENSOR_KEYS[s]
        const matrix = sd[key]
        if (!matrix || !Array.isArray(matrix) || matrix.length === 0) continue
        const colOffset = s * 64
        for (let row = 0; row < 64 && row < matrix.length; row++) {
          for (let col = 0; col < 64 && col < matrix[row].length; col++) {
            // 顺时针旋转 90 度：新行 = col，新列 = 63 - row
            const newRow = col
            const newCol = 63 - row
            ndata[newRow * NY + colOffset + newCol] = matrix[row][col]
          }
        }
      }
    }

    /* ─── 数据更新 ─── */
    function renewData() {
      const { particleParams: pp, transformParams: tp } = propsRef.current
      // 默认值对齐 express 步道场景（particleConfig 的 GAIT_*_DEFAULTS）
      const params = {
        gaussSigma: pp.gaussSigma ?? 2.5,
        filterThreshold: pp.filterThreshold ?? 2,
        initValue: pp.initValue ?? 2.5,
        colorRange: pp.colorRange ?? 700,
        heightScale: pp.heightScale ?? 2.6,
      }

      // 实时更新空间变换（默认值对齐 express 步道 GAIT_TRANSFORM_DEFAULTS）
      const tfScale = tp.scale ?? 1.0
      const s = BASE_SCALE * tfScale
      particles.scale.set(s, s, s)
      material.size = tp.particleSize ?? 3.7
      group.position.set(tp.posX ?? 0, tp.posY ?? 305, tp.posZ ?? 350)
      // rotation（保留原有的 rotation.x = Math.PI/3，叠加用户调节值）
      const deg = Math.PI / 180
      particles.rotation.x = Math.PI / 3 + (tp.rotX ?? 153) * deg
      particles.rotation.y = (tp.rotY ?? 180) * deg
      particles.rotation.z = (tp.rotZ ?? 180) * deg

      mergeSensorData()

      // 坏线补值：在合并后的 64×256 矩阵上做坏线检测和插值修复
      zeroLine64x256(ndata, 64, 256)

      // 过滤
      for (let i = 0; i < ndata.length; i++) {
        if (ndata[i] < params.filterThreshold) ndata[i] = 0
      }

      // 非方阵插值：64×256 → 128×512
      interp1016(ndata, bigArr, NX, NY, INTERP)

      // 添加边界填充
      const bigArrs = addSide(bigArr, NY * INTERP, NX * INTERP, PAD, PAD)

      // 高斯模糊
      gaussBlur_1(bigArrs, bigArrg, AY, AX, params.gaussSigma)

      // 更新粒子位置和颜色
      let k = 0,
        l = 0
      for (let ix = 0; ix < AX; ix++) {
        for (let iy = 0; iy < AY; iy++) {
          const val = bigArrg[l] * 10
          smoothBig[l] += (val - smoothBig[l] + 0.5) / params.initValue

          // 逆时针旋转 90 度：原 (ix,iy) → 新 X=(AY-1-iy), 新 Z=ix
          positions[k] = (AY - 1 - iy) * SEP - padDepth / 2
          positions[k + 1] = smoothBig[l] * params.heightScale
          positions[k + 2] = ix * SEP - padWidth / 2

          const rgb = jet(0, params.colorRange, smoothBig[l])
          colors[k] = rgb[0] / 255
          colors[k + 1] = rgb[1] / 255
          colors[k + 2] = rgb[2] / 255

          k += 3
          l++
        }
      }

      posAttr.needsUpdate = true
      colAttr.needsUpdate = true
    }

    /* ─── 渲染循环 ─── */
    let animId
    function animate() {
      animId = requestAnimationFrame(animate)
      renewData()
      controls.update()
      renderer.render(scene, camera)
    }

    function onResize() {
      let w = container.clientWidth || window.innerWidth * 0.7
      let h = container.clientHeight || window.innerHeight * 0.8
      renderer.setSize(w, h)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
    }

    window.addEventListener('resize', onResize)
    animate()

    return () => {
      cancelAnimationFrame(animId)
      window.removeEventListener('resize', onResize)
      geometry.dispose()
      material.dispose()
      if (renderer) renderer.dispose()
    }
  }, [])

  return (
    <div style={{ width: '100%', height: '100%', position: 'absolute', top: 0, left: 0 }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
    </div>
  )
}
