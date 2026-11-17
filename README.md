# MacMonitor 🌀

**macOS 设备监控 + 风扇自动控温工具**：实时监控 CPU / 内存 / GPU / 网络 / 全部进程，风扇支持恒定转速与传感器联动自动控温。单进程原生界面，常驻 CPU 仅 ~4%。

> ⚠️ **当前仅完整支持 Intel 机型（老款 Mac）**；Apple Silicon（M 系列）适配开发中，后续跟进。

![macOS 13+](https://img.shields.io/badge/macOS-13%2B-blue) ![Intel](https://img.shields.io/badge/Intel-完整支持-green) ![Apple Silicon](https://img.shields.io/badge/Apple%20Silicon-开发中-orange) ![CPU](https://img.shields.io/badge/面板CPU-~4%25-9cf)

**搜索关键词**：`mac 监控` `mac 风扇控制` `Mac Monitor` `Fan Control` `macos 系统监控` `温度监控` `进程管理` `菜单栏工具` `activity monitor` `cpu gpu 内存`

## 运行截图

**菜单栏常驻指标**（转速 / CPU / 内存 / GPU 一目了然）：

<img src="screenshots/menubar.png" width="420">

**监控面板**（风扇控制 / 温度 / 系统指标 / 进程表，表头排序 · 搜索过滤 · 点击行管理进程）：

<img src="screenshots/panel.png" width="560">

**关于 / 打赏**（收款码每 6 小时从 GitHub 自动刷新，替换仓库中的 `donate/wechat-qr.png` 即可更新）：

<img src="screenshots/about.png" width="300">

## 功能

- **进程监控**：全部进程的 CPU / GPU / 网络 / 内存 / 线程，表头点击排序，实时搜索过滤，点行查看详情、退出/强杀进程
- **系统指标**：CPU、内存、GPU（核显/独显利用率）、网速实时展示
- **风扇控制**：三种模式
  - 自动：交还系统控制
  - 恒定：固定转速（滑杆调节）
  - 联动：选定传感器 + 目标温度，自动线性调速（目标 -12°C 起步，目标温度约 60% 转速，+6°C 全速）
- **菜单栏常驻**：🌀转速 CPU% 内存% GPU% 一目了然，点开即完整面板
- **单进程架构**：菜单栏 + 采集服务 + 原生 AppKit 面板合一，内存 ~60MB
- **本地采集**：所有数据只在本地，不上传任何信息

## 安装使用

### 方式一：下载安装包（推荐）

从 [Releases](../../releases) 下载 DMG，拖入「应用程序」，右键打开（首次需允许）。
安装包内置完整 Python 运行时，**无需安装任何依赖**。

首次运行会请求两次管理员授权：

1. 风扇控制（编译 setuid 的 smc 工具）
2. 每进程 GPU/网络监控（powermetrics / nettop 免密 sudo）

### 方式二：源码运行

```bash
# 1) 依赖
pip install -r requirements.txt

# 2) 风扇控制工具（GPL，需自行编译；不装则监控功能正常、风扇只读）
bash scripts/build-smc.sh

# 3) 免密采样（可选，启用每进程 GPU/网络列）
sudo sh -c 'echo "$(whoami) ALL=(root) NOPASSWD: /usr/bin/powermetrics, /usr/bin/nettop" > /etc/sudoers.d/resmon && chmod 440 /etc/sudoers.d/resmon'

# 4) 启动
python app.py
```

## 打包自包含 .app / DMG

```bash
# 独立 Python 运行时 + 依赖（目标机器零依赖）
uv python install 3.12
cp -r "$(uv python find 3.12)" runtime
uv pip install --target site-pkgs -r requirements.txt
cp runtime/bin/python3.12 runtime/bin/MacMonitor
# 组装 .app（Contents/MacOS/MacMonitor 即 app-launcher.sh）后：
hdiutil create -volname MacMonitor -srcfolder dist -ov -format UDBZ MacMonitor.dmg
```

## 平台说明

| 平台 | 状态 |
|---|---|
| Intel macOS 13+ | ✅ 完整功能 |
| Apple Silicon (M 系列) | ⚠️ Rosetta 可运行；SMC 温度/风扇键位与 Intel 不同，传感器与风扇控制需按机型适配 |

## 目录结构

```
app.py             入口：菜单栏常驻 + 单进程装配
panel.py           原生 AppKit 面板（风扇控制/温度/进程表）
server.py          采集后端：进程/GPU/网络/SMC + 内嵌 HTTP API
app-launcher.sh    .app 启动器（自包含运行时 + 首次授权）
web/index.html     浏览器版界面（调试用，127.0.0.1:8765）
scripts/           smc 工具编译脚本
```

## 致谢

- [smcFanControl](https://github.com/hholtmann/smcFanControl) — SMC 读写工具（GPL）
- [rumps](https://github.com/jaraco/rumps)、[psutil](https://github.com/giampaolo/psutil)、[PyObjC](https://github.com/ronaldoussoren/pyobjc)

## 许可

[MIT](LICENSE)（smc 工具本身遵循上游 GPL，需自行编译，不包含在本仓库）

## 打赏

如果这个工具帮到了你，可以请作者喝杯咖啡 ☕️

微信打赏：

![微信打赏](donate/wechat-qr.png)
