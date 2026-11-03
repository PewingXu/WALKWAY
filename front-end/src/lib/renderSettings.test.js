import assert from 'node:assert/strict'
import test from 'node:test'
import { pressureColor, pressureOpacity, transitionFactor, sensorFramePeak } from './renderSettings.js'

test('色阶曲线增强低压颜色，同时保持蓝色起点和红色终点', () => {
  assert.notDeepEqual(pressureColor(40, 255, 0.6), pressureColor(40, 255, 1))
  assert.deepEqual(pressureColor(0, 255, 0.6), pressureColor(0, 255, 2))
  assert.deepEqual(pressureColor(255, 255, 0.6), pressureColor(1024, 255, 2))
  const high = pressureColor(255, 255)
  assert.ok(high[0] > high[1] && high[0] > high[2])
})

test('边缘淡出控制低压透明度，关闭后零压力仍完全透明', () => {
  assert.equal(pressureOpacity(0, 0), 0)
  assert.equal(pressureOpacity(2, 0), 1)
  assert.ok(pressureOpacity(2, 12) < pressureOpacity(2, 6))
  assert.equal(pressureOpacity(20, 12), 1)
})

test('帧间平滑与刷新率无关，最小值立即更新', () => {
  const singleFrame = transitionFactor(3, 1000 / 60)
  const doubleFrame = transitionFactor(3, 1000 / 30)
  assert.ok(Math.abs((1 - singleFrame) ** 2 - (1 - doubleFrame)) < 1e-12)
  assert.ok(transitionFactor(12, 16) < transitionFactor(3, 16))
  assert.equal(transitionFactor(1, 16), 1)
  assert.equal(transitionFactor(3, 0), 0)
  assert.ok(transitionFactor(3, 1000) > 0.99999)
})

test('当前峰值跨四个传感器计算，忽略空帧和非法值', () => {
  assert.equal(sensorFramePeak({ sensor1: null, sensor2: [[12, NaN], [255, Infinity]], sensor3: [[80]] }), 255)
  assert.equal(sensorFramePeak({}), 0)
})
