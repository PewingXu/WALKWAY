/**
 * replayStore.js - 极简模块级单例，存放待回放的 frames。
 *
 * frames 较大（每帧 4×4096 数字），不适合走 router state 序列化，
 * 因此用模块单例在内存中传递给回放页。
 */

let _frames = null
let _meta = null

/**
 * 设置待回放数据。
 * @param {Array} frames - parseWorkbookArrayBuffer 的返回值
 * @param {Object} [meta] - 附加信息，如 { name, fileName }
 */
export function setReplayFrames(frames, meta = null) {
  _frames = frames || null
  _meta = meta || null
}

/** 取待回放帧（可能为 null）。 */
export function getReplayFrames() {
  return _frames
}

/** 取附加信息。 */
export function getReplayMeta() {
  return _meta
}

/** 清空。 */
export function clearReplayFrames() {
  _frames = null
  _meta = null
}
