# 步道足底压力采集系统（walkway）

独立的 Electron 桌面工具：登录（只输密钥）→ 直接进步道采集页 → 实时查看**一整条步道的 3D 压力点云**（4 块垫子拼成 64×256）→ 采集 → **保存 CSV** / **生成报告（PDF）**。也支持**导入已有 CSV（含 1~4.csv 的目录）直接生成报告**。

报告由 `python/foot-template.py` 生成（入口 `create_gait_report`）。

## 目录结构

```
electron/          Electron 主进程
  main.js          创建窗口、fork 设备后端、IPC（保存CSV/生成报告/设备状态）
  preload.js       通过 contextBridge 暴露 window.electronAPI
  pythonRuntime.js 定位内置 Python 运行时并组装环境变量
serial/            精简设备后端（只服务 4 块脚垫）
  gaitSerialServer.js  读串口 → WS 19999 广播 {sitData:{foot1..4}}
  config.js / serialport.js
  serial.txt       MAC→foot1~4 映射（现场配置）
  README.md        设备后端说明 / 单跑测试 / WS 协议
python/
  foot-template.py 报告生成脚本（勿改其算法）
  run_report.py    命令行包装（importlib 加载 foot-template + 强制 Agg 后端）
  requirements.txt 报告依赖
  runtime/         内置 Python 运行时（由 scripts/prepare-python.js 准备，未入库）
front-end/         Vite + React 前端（暗蓝主题）
  src/pages/Login.jsx     登录页（单输入密钥）
  src/pages/Capture.jsx   采集页（4 热力图 / 开始停止 / 保存CSV / 生成报告）
  src/lib/BackendBridge.js  连 ws://localhost:19999
  src/lib/csvExport.js      时间格式化 + CSV 拼装（模板格式）
  src/index.css             暗蓝主题 CSS 变量（换 UI 模板改这里）
assets/            logo.ico / logo.png（当前为占位图，待替换）
scripts/prepare-python.js  准备内置 Python 运行时
```

## 开发运行

```bash
npm install                 # 根依赖（electron / serialport / ws）
cd front-end && npm install # 前端依赖
```

两种方式起前端：

- 常规 dev（vite 热更新 + electron）：
  ```bash
  npm run dev
  ```
  （前端 dev server 端口 5273；electron 读 VITE_DEV_SERVER_URL）

- 或用已构建的 dist（免起 vite）：
  ```bash
  npm run build:front
  WALKWAY_LOAD_DIST=1 npm start
  ```

### 免硬件调试（无步道设备时）

设备后端支持两种合成/回放模式，便于开发时看到热力图：

```bash
WALKWAY_MOCK=1 npm start                 # 合成移动压力斑点，约 77Hz
WALKWAY_REPLAY=<含1~4.csv的目录> npm start # 回放已有数据
```

> 注：若在某些终端遇到 electron 启动报 `Cannot read properties of undefined (reading 'isPackaged')`，
> 是环境里带了 `ELECTRON_RUN_AS_NODE=1`。正常终端不会有；如遇到请先 `unset ELECTRON_RUN_AS_NODE`。

## 设备与 MAC 映射

步道 = 4 块串口压力垫（CH340，3M 波特率，帧尾 `AA 55 03 99`，4096 字节/帧）。
每块垫子的编号（foot1~foot4）由 AT 指令读到的 Unique ID(MAC) 决定，需在 `serial/serial.txt` 里配置映射。
现场识别方法见 `serial/README.md`：从日志 `[mac] ... Unique ID: XXXX` 或前端设备状态读取各块 ID，逐块踩踏确认后填入。未配置时后端会按发现顺序临时分配并打印待补充的映射行。

## 保存 CSV / 生成报告

- **保存 CSV**：把当次采集写成 `1.csv~4.csv`（列 `data,time,max`；`data` 为 4096 值的 list 字符串；`time` 格式 `YYYY/MM/DD HH:mm:ss:SSS`），存到所选目录 —— 即 `foot-template.py` 要求的格式。
- **生成报告**：主进程把 4 份 CSV 写入临时目录，spawn 内置 Python 跑 `run_report.py` → 生成 PDF 并自动打开。
  - 开发期若未准备内置 runtime，可用环境变量指定 Python：
    `WALKWAY_PYTHON=<装好依赖的python.exe> npm start`
  - 报告依赖：numpy / pandas / scipy / matplotlib / opencv-python / reportlab / pillow。
  - 报告脚本依赖中文字体 `C:\Windows\Fonts\msyh.ttc`（Windows 默认存在）。

## 打包（内置 Python）

```bash
# 1. 准备内置 Python 运行时（把一个已装好依赖的 python 目录拷进 python/runtime）
node scripts/prepare-python.js <源runtime目录>
# 2. 构建前端 + 打 Windows 安装包
npm run build
```

- `assets/logo.ico` 目前是占位图标，正式发布前替换为正式图标。
- **serialport 原生模块**：真实设备模式需匹配 Electron 的 ABI。若打包后真实采集报原生模块错误，
  用 `@electron/rebuild` 对 serialport 重建后再打包（MOCK/REPLAY 模式已懒加载，不受影响）。

## 待办 / 需提供

- [ ] 正式应用图标 `assets/logo.ico`（多尺寸）与登录页 `logo.png`（≥512×512）
- [ ] 步道设备的 MAC→foot1~4 映射（填入 `serial/serial.txt`）
- [ ] UI 暗蓝模板（到位后据其调整 `src/index.css` 主题变量与布局）
- [ ] 真实硬件端到端验证（波特率探测 / AT 取 MAC / 实时热力图）
