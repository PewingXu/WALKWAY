/**
 * dataImport.js - 导入 express 导出的 .xlsx / 宽表 .csv，提取"步道"（行走步态评估）数据
 *
 * 数据模型（frames）：
 *   frames = [
 *     { ts: <毫秒时间戳 Number>, tiles: { 1: number[4096]|null, 2: ..., 3: ..., 4: ... } },
 *     ...
 *   ]
 * 每一帧 = 同一时刻 4 块 64×64 压力垫。缺某块则该块为 null。
 *
 * 步道数据定位（稳健）：
 *   优先取名为 "行走步态评估" 的 sheet；否则遍历所有 sheet，
 *   取表头里含 `foot1_data` 列的那个 sheet。
 */
import * as XLSX from 'xlsx'
import { formatTime, buildCsvRow, buildCsv } from './csvExport.js'

const WALKWAY_SHEET_NAME = '行走步态评估'
const TILE_DATA_COLS = ['foot1_data', 'foot2_data', 'foot3_data', 'foot4_data']

/** 把某个 header 行归一化为字符串数组 */
function normHeader(row) {
  return (row || []).map((c) => (c == null ? '' : String(c).trim()))
}

/** 在 workbook 里找到步道 sheet 的 aoa（二维数组）。找不到返回 null。 */
function findWalkwayAoa(wb) {
  const names = wb.SheetNames || []

  // 1) 优先按 sheet 名
  if (names.includes(WALKWAY_SHEET_NAME)) {
    const aoa = XLSX.utils.sheet_to_json(wb.Sheets[WALKWAY_SHEET_NAME], { header: 1 })
    if (aoa && aoa.length) {
      const header = normHeader(aoa[0])
      if (header.includes('foot1_data')) return aoa
    }
  }

  // 2) 遍历所有 sheet，找表头含 foot1_data 的
  for (const nm of names) {
    const aoa = XLSX.utils.sheet_to_json(wb.Sheets[nm], { header: 1 })
    if (!aoa || !aoa.length) continue
    const header = normHeader(aoa[0])
    if (header.includes('foot1_data')) return aoa
  }
  return null
}

/** 安全解析 footN_data 单元格 → number[]（失败或空返回 null） */
function parseDataCell(cell) {
  if (cell == null) return null
  if (Array.isArray(cell)) return cell
  const s = String(cell).trim()
  if (!s) return null
  try {
    const arr = JSON.parse(s)
    return Array.isArray(arr) ? arr : null
  } catch (e) {
    return null
  }
}

/**
 * 解析 ArrayBuffer（.xlsx / .xls / 宽表 .csv 都走 XLSX.read）→ frames。
 * @param {ArrayBuffer} arrayBuffer
 * @returns {{ ts:number, tiles:{1:number[]|null,2:number[]|null,3:number[]|null,4:number[]|null} }[]}
 * @throws {Error} 找不到步道数据时抛出可读错误
 */
export function parseWorkbookArrayBuffer(arrayBuffer) {
  let wb
  try {
    wb = XLSX.read(new Uint8Array(arrayBuffer), { type: 'array' })
  } catch (e) {
    throw new Error('无法解析文件（不是有效的 Excel/CSV）：' + (e && e.message ? e.message : e))
  }

  const aoa = findWalkwayAoa(wb)
  if (!aoa) {
    throw new Error('未找到步道数据：文件中没有"行走步态评估"表，也没有包含 foot1_data 列的表')
  }

  const header = normHeader(aoa[0])
  const idxTs = header.indexOf('timestamp')
  const idxData = TILE_DATA_COLS.map((c) => header.indexOf(c))
  if (idxData[0] < 0) {
    throw new Error('步道数据表缺少 foot1_data 列')
  }

  const frames = []
  for (let r = 1; r < aoa.length; r++) {
    const row = aoa[r]
    if (!row || row.length === 0) continue

    const tiles = { 1: null, 2: null, 3: null, 4: null }
    let hasAny = false
    for (let t = 0; t < 4; t++) {
      const col = idxData[t]
      if (col < 0) continue
      const arr = parseDataCell(row[col])
      if (arr) {
        tiles[t + 1] = arr
        hasAny = true
      }
    }
    if (!hasAny) continue

    // timestamp（毫秒）；缺失时用行序号占位（不影响回放，仅影响 time 显示）
    let ts = idxTs >= 0 ? Number(row[idxTs]) : NaN
    if (!Number.isFinite(ts)) ts = Date.now()

    frames.push({ ts, tiles })
  }

  if (frames.length === 0) {
    throw new Error('步道数据表中没有可解析的帧（foot*_data 均为空）')
  }
  return frames
}

/**
 * frames → 4 个模板 CSV 字符串 { '1','2','3','4' }（列 data,time,max）。
 * time 由该帧 ts 格式化；data 双引号包裹。供 generateReport 使用。
 * @param {ReturnType<typeof parseWorkbookArrayBuffer>} frames
 * @returns {{ '1':string, '2':string, '3':string, '4':string }}
 */
export function framesToTemplateCsvs(frames) {
  const rows = { 1: [], 2: [], 3: [], 4: [] }
  for (const f of frames) {
    const d = new Date(f.ts)
    for (let n = 1; n <= 4; n++) {
      const arr = f.tiles[n]
      if (arr) rows[n].push(buildCsvRow(arr, d))
    }
  }
  return {
    1: buildCsv(rows[1]),
    2: buildCsv(rows[2]),
    3: buildCsv(rows[3]),
    4: buildCsv(rows[4]),
  }
}

/** 一维 4096 数组 → 64×64 转置矩阵（与 Capture 显示一致）。null 返回 null。 */
function toTransposedMatrix(arr) {
  if (!arr || arr.length < 64 * 64) return null
  const raw = []
  for (let r = 0; r < 64; r++) raw.push(arr.slice(r * 64, (r + 1) * 64))
  const matrix = []
  for (let c = 0; c < 64; c++) {
    const rowOut = []
    for (let r = 0; r < 64; r++) rowOut.push(raw[r][c])
    matrix.push(rowOut)
  }
  return matrix
}

/**
 * 某帧 → GaitWalkway 需要的 { sensor1..4 } 转置矩阵（缺块为 null）。
 * @param {{ tiles:Object }} frame
 * @returns {{ sensor1:any, sensor2:any, sensor3:any, sensor4:any }}
 */
export function frameToMatrices(frame) {
  const t = frame && frame.tiles ? frame.tiles : {}
  return {
    sensor1: toTransposedMatrix(t[1]),
    sensor2: toTransposedMatrix(t[2]),
    sensor3: toTransposedMatrix(t[3]),
    sensor4: toTransposedMatrix(t[4]),
  }
}

/** 便捷：把某帧 ts 格式化为报告时间字符串（YYYY/MM/DD HH:mm:ss:SSS）。 */
export function frameTimeStr(frame) {
  return formatTime(new Date(frame.ts))
}
