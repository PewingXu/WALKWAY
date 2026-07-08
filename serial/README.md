# 足底压力步道串口设备服务（serial/）

精简的独立 Node 串口设备服务，只服务足底压力步道的 4 块脚垫（foot1~foot4）。
逐函数移植自参考蓝本 `laonianren-express` 的 `serialServer.js`，但**只保留 4096
字节帧（脚垫）这一条链路**，删除了手套/坐垫/SQLite/express/加密/云端等一切无关部分。

## 文件

| 文件 | 说明 |
|------|------|
| `config.js` | 常量：`splitArr`（帧分隔符 AA 55 03 99）、`BAUD_DEVICE_MAP`（3000000→'foot'） |
| `serialport.js` | `getPort`（win32 只留 wch.cn；已修好蓝本 `os.platform ==` 的 bug）、串口连接（serialport v13 API） |
| `gaitSerialServer.js` | 主服务：波特率探测 → 连接 → 发 AT 取 MAC → 帧处理（方向翻转+去噪+滤波）→ 测帧率 → 定时 WS 广播 |
| `serial.txt` | MAC(Unique ID) → footN 映射（见下） |

依赖：只用 `serialport`(v13) 和 `ws`（已在 `D:\walkway\package.json` 声明）。

## 配置 serial.txt（把 MAC 映射到 foot1~foot4）

`serial.txt` 为 JSON，映射放在 `key` 字段里（可以是对象，也可以是 JSON 字符串）：

```json
{
  "key": {
    "34463730155032138F": "foot1",
    "34463730155032138E": "foot2",
    "34463730155032138D": "foot3",
    "34463730155032138C": "foot4"
  },
  "orgName": "示例机构"
}
```

- 左边是每块脚垫模组的 **Unique ID**（MAC），右边是 `foot1`~`foot4`。
- 比较时忽略大小写、只取字母数字（`normalizeSerialIdentifier`）。
- **现场识别**：连上设备启动服务，看后端日志里每个串口打印的
  `[mac] <port> Unique ID: XXXX`，或看前端收到的 `{macInfo:{footN:"XXXX"}}`，
  逐块踩踏确认位置后把 Unique ID 抄进 `serial.txt`。
- **未命中**的脚垫会按发现顺序**临时分配** foot1..foot4（并在日志里提示应加入的行），
  仅用于临时联调，正式使用请配好映射。

## 单独运行 / 免硬件测试

无需真实硬件即可让前端联调：

```bash
# 合成数据模式：4 块脚垫各一个随时间移动的高斯压力斑点，约 77Hz 广播
WALKWAY_MOCK=1 node serial/gaitSerialServer.js

# 回放模式：回放 <目录>/1.csv~4.csv（列 data,time,max，data 为 4096 值 list 字面量）
WALKWAY_REPLAY=/path/to/csvdir node serial/gaitSerialServer.js
# 读不到 1.csv~4.csv 时自动退回 MOCK 行为

# 真实硬件模式（默认，需要脚垫接入）
node serial/gaitSerialServer.js
```

可选环境变量：
- `WALKWAY_REGION=gz|bj`：矩阵方向地区开关，默认 `gz`（广州）。`bj`（北京）分支已保留。
- `WALKWAY_SERIAL_TXT=<path>`：自定义 serial.txt 路径（默认脚本同目录）。

## WebSocket 协议（端口 19999）

服务用 `ws` 起 `new WebSocket.Server({ port: 19999 })`，向所有客户端广播 JSON 文本。

脚垫数据消息（**只广播当前在线的 tile**）：

```json
{"sitData":{
   "foot1":{"status":"online","arr":[/* 4096 个整数 */],"stamp":1720000000000,"HZ":77},
   "foot2":{"status":"online","arr":[...],"stamp":...,"HZ":...}
}}
```

- `arr` 是经过**方向翻转 + 去噪 + 滤波**后的原始一维 4096 数组，**不转置**（转置由前端显示时做）。
- `stamp` 为毫秒时间戳；`HZ` 为测得帧率（每秒帧数）。

设备信息消息（可选，用于现场识别 MAC）：

```json
{"macInfo":{"foot1":"34463730155032138F","foot2":"..."}}
```

## 与 Electron 主进程约定

- 由主进程 `fork()` 启动，`stdio` 含 `'ipc'`。
- 启动成功后 `process.send({ type:'ready', port:19999 })`。
- 收到 `{type:'shutdown'}` 或 `SIGTERM`：关闭所有串口与 WS，然后 `process.exit(0)`。
- 出错时 `process.send({ type:'error', message })` 并打印 stderr。

## 已知限制 / 需真实硬件验证的部分

- 波特率探测、AT 取 MAC、`Unique ID` 回帧解析、4096 帧翻转/滤波、真实帧率测定等
  逻辑无法在无硬件时验证，只做了语法检查和 MOCK/REPLAY 联调。
- 广播频率 HZ 在真实模式下由首 30 帧测定；无稳定帧时用兜底 30Hz。
- 坏线补值（zeroLineRepair）在步道模式下按蓝本约定**不在单 tile 上做**，由前端合并
  64×256 后处理。
