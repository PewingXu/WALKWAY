/**
 * xlsxExport.js - 把采集到的整帧数据导出成与 express 一致的宽表 .xlsx
 *
 * sheet 名："行走步态评估"
 * 列顺序（严格）：
 *   timestamp, date, assessment_id, sample_type,
 *   foot1_pressure, foot1_area, foot1_max, foot1_min, foot1_avg, foot1_data,
 *   foot2_...(6列), foot3_...(6列), foot4_...(6列)
 *
 * 每块统计：
 *   pressure = sum(arr)
 *   area     = count(arr > 0)
 *   max/min  = for 循环求（避免 Math.max(...arr) 爆栈）
 *   avg      = sum / 4096，保留 2 位小数
 *   data     = '[' + arr.join(',') + ']'（SheetJS 直接写字符串，无需再包双引号）
 */
import * as XLSX from 'xlsx'

const TILE_FIELDS = ['pressure', 'area', 'max', 'min', 'avg', 'data']

function pad2(n) {
  return n < 10 ? '0' + n : '' + n
}

/** ts(ms) → 'YYYY-MM-DD' */
function toDateStr(ts) {
  const d = new Date(ts)
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

/** 计算一块 4096 数组的统计。arr 为 null 时返回全空。 */
function tileStats(arr) {
  if (!arr || arr.length === 0) {
    return { pressure: '', area: '', max: '', min: '', avg: '', data: '' }
  }
  let sum = 0
  let area = 0
  let max = -Infinity
  let min = Infinity
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i]
    sum += v
    if (v > 0) area++
    if (v > max) max = v
    if (v < min) min = v
  }
  if (max === -Infinity) max = 0
  if (min === Infinity) min = 0
  const avg = Number((sum / 4096).toFixed(2))
  return {
    pressure: sum,
    area,
    max,
    min,
    avg,
    data: '[' + arr.join(',') + ']',
  }
}

/** 构建表头（aoa 首行） */
function buildHeader() {
  const header = ['timestamp', 'date', 'assessment_id', 'sample_type']
  for (let n = 1; n <= 4; n++) {
    for (const f of TILE_FIELDS) header.push(`foot${n}_${f}`)
  }
  return header
}

/**
 * rows → base64 的 xlsx 字符串。
 * @param {{ ts:number, foot1:number[]|null, foot2:..., foot3:..., foot4:... }[]} rows
 * @returns {string} base64
 */
export function buildWalkwayXlsxBase64(rows) {
  const header = buildHeader()
  const startMs = rows.length ? rows[0].ts : Date.now()
  const assessmentId = `gait_${startMs}`

  const aoa = [header]
  for (const row of rows) {
    const ts = row.ts
    const line = [ts, toDateStr(ts), assessmentId, 5]
    for (let n = 1; n <= 4; n++) {
      const st = tileStats(row[`foot${n}`])
      line.push(st.pressure, st.area, st.max, st.min, st.avg, st.data)
    }
    aoa.push(line)
  }

  const ws = XLSX.utils.aoa_to_sheet(aoa)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, '行走步态评估')
  return XLSX.write(wb, { bookType: 'xlsx', type: 'base64' })
}

/** ts(ms) → 'YYYY-MM-DD'（导出文件名用） */
export function dateStrFromTs(ts) {
  return toDateStr(ts)
}
