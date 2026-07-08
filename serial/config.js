/**
 * 步道串口设备配置（从参考 back-end/code/util/config.js 精简移植）
 *
 * 本服务只服务足底压力步道（4 块脚垫 foot1~foot4）：
 *   - foot1~foot4（脚垫）：波特率 3000000，4096 字节帧（64×64 矩阵），
 *     通过 AT 指令获取模组 Unique ID(MAC)，再查 serial.txt 映射表区分编号。
 *
 * 识别流程：
 *   1. 枚举所有串口（win32 只留 manufacturer=='wch.cn'，见 serialport.js）
 *   2. 对每个串口做波特率探测（本服务候选表见 gaitSerialServer.js）
 *   3. 双重验证：先检测分隔符 AA 55 03 99，再验证帧长度是否匹配（脚垫=4096）
 *   4. 3M 波特率 → 设备大类 'foot'
 *   5. 发 AT 指令取 MAC，查 serial.txt 映射表细分为 foot1~foot4
 */

// 波特率 → 设备大类映射（本服务只保留脚垫一条链路）
const BAUD_DEVICE_MAP = {
  3000000: 'foot', // 脚垫（foot1-4 由 MAC 地址区分）
}

const constantObj = {
  // 帧分隔符：AA 55 03 99（与参考完全一致，切帧用）
  splitArr: [0xaa, 0x55, 0x03, 0x99],
  BAUD_DEVICE_MAP,
}

module.exports = constantObj
