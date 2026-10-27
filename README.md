# 2.4G 无线外设电量小工具

**Windows 任务栏电量和桌面面板，直接读罗技 LIGHTSPEED 接收器的电量 —— 纯 Python 标准库 + ctypes，零第三方依赖，不需要装罗技 G HUB。**

**自动检测**：插入罗技接收器后按协议自动识别（不挑型号、不用改代码），
自动扫描配对设备所在槽位并匹配，设备名直接从硬件实读。

实测设备：罗技 GPW2（G PRO X SUPERLIGHT 2 SE）无线鼠标 + `046D:C547` LIGHTSPEED 接收器。

```
python my_devices.py --once
PRO X SUPERLIGHT 2 SE: 50%  [放电中]
```

## 为什么用它

- **零依赖**：只用 `ctypes` 调 Windows DLL 和 `tkinter` 画界面。不装 hidapi / pysystray / Pillow / pywin32。
- **不装驱动**：直接跟接收器说 HID++ 协议，G HUB / Options 装不装都无所谓。
- **不联网**：全程本地读 HID 设备，**不写任何日志文件**，不产生后台残留。
- **CPU 几乎为零**：空闲实测 75 秒消耗 `0.0000` 秒 CPU（事件驱动，不轮询）。
- **协议过程全公开**：`probes/` 里是从零逆向的每一步，包括走过的弯路。

## 安装

需要 **Windows 10/11**。

**方式 A：便携版 exe（不需要 Python）**

去 [Releases](https://github.com/Arrebol-7/logitech-battery-tray/releases) 下载
`LogitechBatteryTray.exe`（单文件，约 13 MB），双击即用：

| 双击 | 效果 |
|---|---|
| `LogitechBatteryTray.exe` | 打开电量面板 |
| `LogitechBatteryTray.exe --tray` | 托盘常驻（可给它在桌面建个快捷方式，参数写在「目标」栏） |

**方式 B：源码运行**（需要 Python 3.8+，官方安装包默认自带 tkinter）

```bash
git clone https://github.com/Arrebol-7/logitech-battery-tray.git
cd logitech-battery-tray

python my_devices.py --once     # 先确认能读到你的设备
```

没有依赖要装，克隆下来就能跑。

### 自己打包 exe（可选）

```bash
pip install pyinstaller
python _make_ico.py             # 生成应用图标 app.ico（纯标准库画圆环）
pyinstaller --noconfirm --clean --onefile --windowed \
    --name LogitechBatteryTray --icon app.ico \
    --hidden-import tray --hidden-import ui_theme \
    --hidden-import shell --hidden-import hidpp my_devices.py
# 产物在 dist/LogitechBatteryTray.exe
```

## 启动方式

### 一、命令行（最直接）

```bash
python my_devices.py --once      # 读一次，打印一行就退出
python my_devices.py             # 简洁面板（图形卡片窗口）
python my_devices.py --tray      # 托盘常驻，图标上直接显示电量数字
python my_devices.py --debug     # 调试版：打印完整协议诊断信息
```

### 二、双击（不用开命令行）

```bash
python my_devices.py --make-shortcuts    # 在桌面生成下面两个图标
```

| 桌面图标 | 双击后 |
|---|---|
| **启动电量托盘** | 直接进托盘常驻，右下角通知区域出现电量数字 |
| **2.4G电量工具** | 打开项目文件夹（真链接，可进去看源码） |

### 三、开机自动启动

```bash
python my_devices.py --install-autostart     # 安装开机自启
python my_devices.py --uninstall-autostart   # 取消开机自启
```

装好后开机托盘自动出现，**不弹黑框**。

### 四、重建桌面链接

```bash
python my_devices.py --remove-shortcuts    # 删除桌面链接
```

---

## 三种形态

| 形态 | 命令 | 说明 |
|---|---|---|
| **简洁面板** | `python my_devices.py` | 深色卡片 + **彩色圆环电量表**（12 点起顺时针填充），环内大号数字，环下状态；右侧为设备名 / 状态徽章 / 读取方式 / 槽位 / 电池等级 / 接收器固件；色阶 ≥60 绿 · 30–59 黄 · <30 红 · 充电中青；默认每 60 秒自动刷新。**卡片随插拔动态增删**，底部「重新检测」可随时强制重扫 |
| **托盘常驻** | `python my_devices.py --tray` | 通知区域图标为**圆环 + 电量数字**（取电量最低的设备，与面板同色阶，按系统 DPI 渲染）；左键刷新、右键开面板、悬停显示「设备名：电量（更新时间）」；**接收器插拔自动感知**（WM_DEVICECHANGE 触发重扫） |
| **调试版** | `python my_devices.py --debug` | 打印自动发现过程、逐槽位扫描结果、协议响应原始字节、特性索引、固件版本等完整诊断 |

面板外观集中在 `ui_theme.py`（配色常量 + `BatteryRing` 圆环控件 + `Card` / `flat_button`），改风格只动这一个文件。

面板与托盘都不需要管理员权限。

**托盘特性**：只跑一个实例（重复启动不会出现第二个图标）；空闲时用 `SetTimer`
驱动刷新，不轮询、不空转；退出时进程真正结束，不留僵尸进程。
读取带互锁（面板 `_busy` / 托盘 `_busy`）：上一次读取没结束就跳过这一次，
避免并发访问同一个 HID 句柄导致响应错位（实测会读到 `0x0F`/“充电将满”这类垃圾值）。

---

## 开机自启原理

`--install-autostart` 在启动文件夹创建一个 `.lnk`：

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\电量托盘.lnk
    目标: pythonw.exe
    参数: "D:\2.4g电量显示插件\start_tray.pyw"
```

用 `pythonw.exe` 启动，**不会弹出控制台黑框**。装好后在任务管理器的
「启动」标签里能看到「电量托盘」这一项。

> `.lnk` 是纯 ctypes 手写 COM（`IShellLinkW` + `IPersistFile`）创建的，
> 没有用 pywin32 / comtypes。

---

## 桌面链接的原理（不是复制）

桌面上的两个图标是**真链接**，指向项目目录，**不复制任何脚本内容**：

| 链接 | 类型 | 指向 |
|---|---|---|
| `2.4G电量工具` | 目录链接 | `D:\2.4g电量显示插件`（可双击进去） |
| `启动电量托盘.lnk` | 快捷方式 | `start_tray.pyw` |

优先用**符号链接**；当账户没有符号链接权限时自动降级：

```
符号链接 ──无权限──> junction（目录）/ 硬链接（文件）
                            │
                    跨卷时不可用 ──> .lnk 快捷方式（仍是指针）
```

本机实测：项目在 `D:`、桌面在 `C:`（跨卷），且账户无符号链接权限，
因此目录用了 **junction**、文件用了 **.lnk** —— 两者在资源管理器里
表现一致，都不占额外空间。

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `my_devices.py` | 主程序，三种形态 + 自动发现/缓存 + CLI 参数 + 面板单实例 |
| `ui_theme.py` | **界面外观**：深色配色常量、`BatteryRing` 圆环电量控件、`Card` 卡片、扁平按钮 |
| `hidpp.py` | **核心**：HID 设备打开、HID++ 1.0/2.0 协议、电量读取、接收器发现与槽位扫描 |
| `tray.py` | 托盘实现（纯 ctypes 手写 `Shell_NotifyIconW` + GDI 画图标 + 单实例锁） |
| `shell.py` | 纯 ctypes：创建 `.lnk` 快捷方式 与 桌面真链接（符号链接/junction/硬链接） |
| `start_tray.pyw` | 开机自启入口（无控制台） |
| `run_once.pyw` | 桌面「查看鼠标电量」链接目标（弹窗显示一次读数） |
| `verify_lnk.py` | 验证 `.lnk` 能被 Windows 正确解析（自测用） |
| `accept_all.py` | **一键验收**：跑完 8 条验收标准（含连续启停 3 次无残留） |
| `accept_cpu.py` | 验收：空闲 75 秒 CPU 占用 |
| `PROBE-FINDINGS.md` | **协议逆向全过程与实测结论**（想看原理看这个） |
| `probes/` | 逆向过程中的探测脚本，含 `probes/README.md` 逐脚本说明 |
| `PROMPT.md` | 最初的需求文档（项目是怎么被要求的） |

---

## 自动检测原理

不再硬编码 VID:PID / 槽位，三层都是动态的：

1. **接收器发现**：枚举 046D 全部 HID 接口，对每个 mi_02&col01 短报文通道
   试一次 1.0 固件寄存器交换（`10 FF 81 F1`）——能应答的就是接收器。
   键盘 / 鼠标本体的接口要么打不开要么不应答，天然被排除。
2. **槽位扫描**：对 col02 长报文通道逐槽位（1~6）发 HID++ 2.0 root.ping，
   应答的槽位 = 在线设备；空槽完全静默。首个在线槽位作为工作槽位。
3. **设备识别**：实读 `0x0005 FRIENDLY_NAME` 得到真实设备名
   （如 "PRO X SUPERLIGHT 2 SE"），失败时回退 `0x0003`。

发现结果有缓存（避免每轮刷新都全扫描）；空结果 10 秒过期自动重扫，
所以**先开工具再插接收器也能自动出现**。托盘还会监听
`WM_DEVICECHANGE`，插拔接收器立即触发重扫。

## 适配其他设备

协议层已做到**免配置自动识别**，无需改代码。当前仅在下列设备上实测：

- `046D:C547` LIGHTSPEED 接收器 + GPW2 鼠标（槽位 1）

其它罗技接收器（Unifying `C52B`、Bolt `C548` 等）理论上同样适配——
发现靠协议应答而非 PID 白名单——但**未逐一实测**。若某型号不工作：

1. `python my_devices.py --debug` 看自动发现与逐槽位扫描输出
2. 若接收器没被识别：跑 `python probes/probe_discovery.py`，
   看它的 col01 是否应答固件寄存器交换
3. USB 接收器（短报文）和蓝牙直连（HID++ over BLE）的通道细节不同，
   蓝牙场景需要调整通道选择逻辑

---

## 许可

MIT License，见 [LICENSE](LICENSE)。

`probes/` 与 `PROBE-FINDINGS.md` 中的协议知识参考了开源项目
[Solaar](https://github.com/pwr-Solaar/Solaar) 的公开实现（GPL-2.0），
仅作对照学习，**未复制其代码**；本项目代码为独立实现，采用 MIT 许可。

---

## 已知行为

- **鼠标休眠时读不到电量**：GPW2 闲置几分钟会挂起，此时设备不响应任何请求。
  动一下鼠标即可恢复。工具会提示"请动一下鼠标唤醒"，不会崩溃。
- **需要接收器已插入**：提示会明确区分"接收器未找到"和"设备休眠"。
- 无需安装罗技 G HUB 或 Options 驱动。

---

## 技术要点

- **协议**：HID++ 1.0（接收器）+ HID++ 2.0（设备特性 `0x1004 UNIFIED_BATTERY`）
- **两个通道分工**：
  - `mi_02&col01` / Report ID `0x10` / 7 字节 → 接收器自身寄存器
  - `mi_02&col02` / Report ID `0x11` / 20 字节 → **转发给设备**（2.0 请求必须走这里）
- **依赖**：仅 `ctypes`、`tkinter`、`os`、`sys`、`time`、`struct`、`datetime` 等标准库

详细协议见 `PROBE-FINDINGS.md`。