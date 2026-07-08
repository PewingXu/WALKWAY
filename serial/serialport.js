// 懒加载 serialport 原生模块：getPort 只依赖 os，MOCK/REPLAY 模式无需加载原生模块。
const os = require('os')

/**
 * 返回所有可用的串口（从参考 back-end/code/util/serialport.js 移植）
 *
 * 【移植修正】参考原文写的是 `os.platform ==`（漏了函数调用括号，是 bug，
 * 永远为 false，导致 filter 永不生效）。这里改成正确的 `os.platform() === 'win32'`。
 *
 * @param {Array} ports SerialPort.list() 返回的全部串口信息
 * @returns {Array} 筛选后的串口列表
 */
const getPort = (ports) => {
  if (os.platform() === 'win32') {
    // Windows：CH340/CH343 芯片厂商为 wch.cn
    return ports.filter((port) => port.manufacturer === 'wch.cn')
  } else if (os.platform() === 'darwin') {
    // macOS：过滤 usb 串口
    return ports.filter((port) => port.path.includes('usb'))
  } else {
    return ports
  }
}

/**
 * 创建串口连接
 *
 * 【移植修正】serialport v13 的正确构造是对象参数形式：
 *   new SerialPort({ path, baudRate, autoOpen:true })
 * 不是老式的 new SerialPort(path, opts, cb)。
 *
 * @param {Object} args
 * @param {string} args.path     串口路径
 * @param {Object} args.parser   数据解析器（DelimiterParser）
 * @param {number} args.baudRate 波特率（脚垫为 3000000）
 * @returns {SerialPort|undefined} 串口连接实例
 */
const newSerialPortLink = ({ path, parser, baudRate = 3000000 }) => {
  const { SerialPort } = require('serialport')
  let port
  console.log('[serialport] open', path, '@', baudRate)
  try {
    port = new SerialPort(
      { path, baudRate, autoOpen: true },
      function (err) {
        if (err) console.log('[serialport] open err:', err.message)
      }
    )
    // 管道添加解析器
    port.pipe(parser)
  } catch (e) {
    console.log('[serialport] open exception:', e && e.message)
  }
  return port
}

module.exports = {
  getPort,
  newSerialPortLink,
}
