# macOS 打包交付指南（步道采集系统）

> 目标：打成一个 `.dmg` 安装包，**内含 Python 算法与运行环境**，对方 Mac 无需另装任何东西。

## 一、前提（决定今天能否交付）

1. **必须在 Mac 上打包。** Windows 打不出 macOS 的 `.app/.dmg`。手上要有一台 Mac（Apple Silicon 或 Intel），或云 Mac。
2. **账号（签名/公证）：**
   - 要“对方双击直接开、无安全警告” → 需 **Apple Developer 账号（$99/年）** 做代码签名 + 公证。注册审核可能几小时~1 天。
   - **今天最快 = 打未签名包**（下面的流程，不需要账号）。对方首次打开时**右键 → 打开**，或终端执行一次：
     ```bash
     xattr -cr "/Applications/步道采集系统.app"
     ```
3. 目标架构：默认 **arm64（Apple Silicon）**。若目标机是 Intel，把 `package.json` 里 `build.mac.target[].arch` 改成 `x64`。

## 二、Mac 上的步骤

```bash
# 0) 装 Node 18+/20、Xcode Command Line Tools（xcode-select --install）

# 1) 取代码
git clone https://github.com/PewingXu/WALKWAY.git
cd WALKWAY
npm install
npm --prefix front-end install

# 2) 准备内置 Python（下载自包含 CPython3.13 + 报告依赖 → python/runtime）
bash scripts/prepare-python-mac.sh
#   跑完会打印“报告依赖齐全”。若下载 404，按提示到 releases 页挑一个 3.13 的
#   install_only 包，把 URL 赋给 PY_URL 再跑：PY_URL=<url> bash scripts/prepare-python-mac.sh

# 3) 为 Electron 重建 serialport 原生模块（真机采集需要；MOCK/回放不需要）
npm run rebuild:serial

# 4) 打包（先构建前端，再出 dmg）
npm run build:mac
#   产物在 dist/ 下：步道采集系统-<版本>-arm64.dmg
```

## 三、交付与首次打开

- 把 `dist/步道采集系统-*.dmg` 发给对方。
- 未签名包首次打开：**右键点 app → 打开 → 再点“打开”**；或终端 `xattr -cr <app路径>`。
- 真机采集：需在登录页密钥里配置 4 块垫子的 MAC→foot1~4 映射（否则按发现顺序临时分配）。

## 四、已处理的坑

- **中文字体**：报告的字体加载已做跨平台（内置 msyh → mac 系统中文字体 PingFang/Heiti → reportlab 内置 CID 中文），mac 上 PDF/图表中文正常显示，无需额外装字体。
- **serialport 原生模块**：`package.json` 已配置 `asarUnpack`，配合 `npm run rebuild:serial`，打包后真机采集可用。
- **Python 无头出图**：报告脚本已强制 matplotlib `Agg` 后端。

## 五、可选：正式签名 + 公证（有 Apple 账号后）

1. `package.json` 的 `build.mac.identity` 改为你的 “Developer ID Application: 名称 (TEAMID)”。
2. 配置公证凭据（环境变量 `APPLE_ID` / `APPLE_APP_SPECIFIC_PASSWORD` / `APPLE_TEAM_ID`），electron-builder 会自动 notarize。
3. 重新 `npm run build:mac`。此后对方双击即可打开、无警告。
