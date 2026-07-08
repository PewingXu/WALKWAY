/**
 * csvExport.js - CSV 格式化与拼装工具
 *
 * 关键约定（必须与报告脚本严格一致）：
 *  1. time 格式为 `YYYY/MM/DD HH:mm:ss:SSS`，即毫秒前用**冒号**分隔，毫秒补足 3 位。
 *     例：2026/07/07 10:20:30:123
 *  2. data 单元格用双引号包裹：`"[a,b,c,...]"`，内部由 JSON.stringify(arr) 生成（无空格、无引号）。
 *  3. max 用 for 循环求，避免 Math.max(...arr) 在 4096 长数组上爆栈。
 *  4. CSV 表头固定为 `data,time,max`。
 */

/** 两位补零 */
function pad2(n) {
  return n < 10 ? '0' + n : '' + n
}

/** 三位补零（毫秒） */
function pad3(n) {
  if (n < 10) return '00' + n
  if (n < 100) return '0' + n
  return '' + n
}

/**
 * 把 Date 格式化为 `YYYY/MM/DD HH:mm:ss:SSS`（毫秒前用冒号）。
 * @param {Date} [d=new Date()]
 * @returns {string}
 */
export function formatTime(d = new Date()) {
  const Y = d.getFullYear()
  const M = pad2(d.getMonth() + 1)
  const D = pad2(d.getDate())
  const h = pad2(d.getHours())
  const m = pad2(d.getMinutes())
  const s = pad2(d.getSeconds())
  const ms = pad3(d.getMilliseconds())
  return `${Y}/${M}/${D} ${h}:${m}:${s}:${ms}`
}

/**
 * 安全求最大值（避免 Math.max(...arr) 在大数组上爆栈）。
 * @param {number[]} arr
 * @returns {number}
 */
export function safeMax(arr) {
  let max = -Infinity
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] > max) max = arr[i]
  }
  if (max === -Infinity) max = 0
  return max
}

/**
 * 生成单帧 CSV 行文本：`"[..arr..]",<time>,<max>`
 * data 双引号包裹；time 为传入时刻（默认帧到达时刻）；max 由 safeMax 计算。
 * @param {number[]} arr - 原始一维数组
 * @param {Date} [date=new Date()] - 帧到达时刻
 * @returns {string}
 */
export function buildCsvRow(arr, date = new Date()) {
  const dataCell = '"' + JSON.stringify(arr) + '"'
  const timeStr = formatTime(date)
  const max = safeMax(arr)
  return `${dataCell},${timeStr},${max}`
}

/**
 * 把某块累积的行数组拼成完整 CSV 字符串（含表头，末尾带换行）。
 * @param {string[]} rows
 * @returns {string}
 */
export function buildCsv(rows) {
  return 'data,time,max\n' + rows.join('\n') + '\n'
}
