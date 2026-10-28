"""2.4G 无线外设电量小工具 —— 主程序。

形态（PROMPT.md §一.2）:
    python my_devices.py --once        打印一次电量并退出（验收标准 1）
    python my_devices.py               简洁面板（tkinter 卡片）  §一.2a
    python my_devices.py --tray        托盘常驻（图标显示电量数字）§一.2b
    python my_devices.py --debug       完整调试版（枚举/转储/探测）§一.2c
    python my_devices.py --install-autostart   写启动文件夹快捷方式 §一.5
    python my_devices.py --remove-autostart    移除启动项
    python my_devices.py --make-shortcuts      桌面建目录链接 + 文件链接 §一.6
    python my_devices.py --remove-shortcuts    移除桌面链接

硬性约束（PROMPT.md §一.1）: 纯标准库 + ctypes，无第三方依赖，不写日志文件（§一.7）。
"""
from __future__ import annotations

import argparse
import datetime
import os
import queue
import sys
import threading
import time
import tkinter as tk

APP_NAME = "电量托盘"
APP_TITLE = "无线外设电量"
HERE = os.path.dirname(os.path.abspath(__file__))
STARTUP_SCRIPT = os.path.join(HERE, "start_tray.pyw")
DEFAULT_INTERVAL = 60
MIN_INTERVAL, MAX_INTERVAL = 30, 1800
MIN_PANEL_WIDTH = 460    # 面板最小逻辑宽度（px @96DPI，随 DPI 缩放）——用户反馈界面太小太挤

# 自动检测：设备清单不再硬编码 —— 插入 2.4G 接收器后按协议识别并自动匹配。
# 缓存策略：发现结果缓存（避免每轮刷新都做 5s 全扫描）；空结果 10s 过期，
# 所以「启动时没插、后来插上」也能在下一轮刷新自动出现。
RESCAN_EXPIRE = 10.0
_RECEIVER_CACHE = None
_RECEIVER_CACHE_AT = 0.0
_DEV_KEY_SEQ = 0
_LAST_RECEIVERS = []      # 最近一次扫描见到的接收器 [(vid, pid, fw)]，与在线无关

DEBUG = False


def log(msg):
    """§一.7：只在 --debug 时输出，不写任何日志文件。"""
    if DEBUG:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- 自动发现
def discover_devices(rescan=False):
    """自动检测连入的 2.4G 接收器与在线设备，返回设备信息列表。

    - 无 PID 白名单：1.0 固件寄存器应答的接口 = 接收器（协议识别）
    - col02 逐槽位 root.ping 找在线设备，设备名用 FRIENDLY_NAME 实读
    - 结果缓存；空结果 RESCAN_EXPIRE 秒后过期自动重扫（插上即自动出现）
    """
    global _RECEIVER_CACHE, _RECEIVER_CACHE_AT, _DEV_KEY_SEQ, _LAST_RECEIVERS
    if rescan:
        _RECEIVER_CACHE = None
    if _RECEIVER_CACHE is not None and (
            _RECEIVER_CACHE or time.time() - _RECEIVER_CACHE_AT < RESCAN_EXPIRE):
        return _RECEIVER_CACHE
    from hidpp import ReceiverBattery, discover_receivers, scan_slots
    devs = []
    recvs = []
    for vid, pid, sp, lp, fw in discover_receivers():
        recvs.append((vid, pid, fw))
        b = ReceiverBattery()
        if not b.open_paths(sp, lp):
            log(f"接收器 {vid:04X}:{pid:04X} 打开失败: {b.last_error}")
            continue
        try:
            hits = [s for s, resp in scan_slots(b.long).items() if resp is not None]
            b.online_slots = hits
            for slot in hits:
                b.slot = slot
                name = b.read_device_name() or f"2.4G 设备（槽位 {slot}）"
                _DEV_KEY_SEQ += 1
                devs.append({"key": f"dev{_DEV_KEY_SEQ}", "name": name,
                             "vid": vid, "pid": pid, "slot": slot,
                             "short_path": sp, "long_path": lp, "firmware": fw})
                log(f"发现设备: {name} @ {vid:04X}:{pid:04X} 槽位 {slot}")
        finally:
            b.close()
    _LAST_RECEIVERS[:] = recvs
    _RECEIVER_CACHE = devs
    _RECEIVER_CACHE_AT = time.time()
    return devs


def receivers_seen():
    """最近一次扫描见到的接收器（含下挂设备全部休眠的情况）。"""
    discover_devices()
    return list(_LAST_RECEIVERS)


def rescan_devices():
    """插拔接收器后强制重扫（托盘 WM_DEVICECHANGE / 面板「重新检测」调用）。"""
    discover_devices(rescan=True)


# ---------------------------------------------------------------- 读取
def read_one(dev):
    """读一台设备的电量，返回 dict 或 None。

    dev 来自 discover_devices()：含接收器接口路径与槽位，不再依赖硬编码表。
    """
    from hidpp import ReceiverBattery
    b = ReceiverBattery()
    b.vid, b.pid, b.slot = dev["vid"], dev["pid"], dev["slot"]
    try:
        if not b.open_paths(dev["short_path"], dev["long_path"]):
            log(f"{dev['name']}: 打开失败 {b.last_error}")
            return None
        data = b.read_battery()
        if data is None:
            log(f"{dev['name']}: {b.last_error}")
            return None
        fw = dev.get("firmware") or b.read_firmware()
        conn = b.read_connection_count()
        data.update({"key": dev["key"], "name": dev["name"],
                     "firmware": fw, "connected": conn, "slot": dev["slot"]})
        log(f"{dev['name']}: {data['percent']}% {data['state_text']} "
            f"level={data['level_text']} raw={data['raw']}")
        return data
    finally:
        b.close()


def read_all(rescan=False):
    return [d for d in (read_one(x) for x in discover_devices(rescan)) if d]


def color_for(pct):
    """§五.2：≥60 绿 / 30–59 黄 / <30 红。"""
    if pct is None:
        return "#888888"
    if pct >= 60:
        return "#2e9e4f"      # 绿
    if pct >= 30:
        return "#d9a300"      # 黄
    return "#d64545"          # 红


def icon_color_for(pct):
    """托盘图标背景色（BGR），与面板同色阶。"""
    if pct is None:
        return 0x00808080
    if pct >= 60:
        return 0x00509E2E
    if pct >= 30:
        return 0x0000A3D9
    return 0x004545D6


# ---------------------------------------------------------------- 形态 1: --once
def cmd_once():
    rows = read_all()
    if not rows:
        # 区分两种失败：接收器不在 / 设备休眠（receivers_seen 由同一轮扫描填充）
        recvs = receivers_seen()
        if not recvs:
            print("未检测到 2.4G 接收器 —— 请确认接收器已插入 USB 口")
            print("  （罗技 046D 接收器自动识别，无需指定型号）")
        else:
            vids = ", ".join(f"{v:04X}:{p:04X}(固件 {fw})" for v, p, fw in recvs)
            print(f"接收器在线：{vids}")
            print("  但下挂设备无响应 → 请动一下鼠标唤醒后再试。")
        return 1
    for r in rows:
        chg = "（充电中）" if r["charging"] else ""
        pct = f"{r['percent']}%" if r["percent"] is not None else r["level_text"]
        print(f"{r['name']}: {pct}{chg}  [{r['state_text']}]")
    return 0


# ---------------------------------------------------------------- 形态 2: 简洁面板
_PANEL = None          # 单实例：§一.3「已开则不重复开」


class Panel:
    """简洁面板：深色卡片 + 彩色圆环，接近手机电池 App 观感。

    信息层级（从上到下）:
        标题栏  设备名 + 实时状态徽章
        主体    圆环大号电量 + 状态/来源/固件明细
        底栏    开机自启 · 刷新间隔 · 立即刷新 · 重新检测 · 退出

    设备卡片是动态的：自动检测到设备就建卡；设备休眠时卡片置灰保留；
    接收器被拔出时对应卡片移除（WM_DEVICECHANGE / 「重新检测」触发重扫）。
    """

    def __init__(self):
        import ui_theme as T
        self.T = T
        # 高分屏清晰 + 像素尺寸随 DPI 等比缩放（用户反馈「界面小且挤」）：
        # 必须在 tk.Tk() 之前声明 DPI aware —— Tk 在创建时读取屏幕 DPI 定缩放，
        # 之后再声明就晚了（Tk 内部仍按 96 DPI 布局，系统把整窗拉伸 1.5x，模糊且挤）。
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE_V2
        except Exception:                                  # noqa: BLE001 — 已声明/老系统回退
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.resizable(False, False)
        self.root.configure(bg=T.BG)
        T.set_scaling(self.root)
        self._s = T.S
        self.result_q = queue.Queue()
        self.cards = {}
        self._busy = False                # 读取互锁：防止并发访问 HID 导致响应错位
        self.interval = DEFAULT_INTERVAL
        self._building = True
        self._gb = {}                     # 圆环想定色：pct -> 目标颜色
        self._anim = None
        self._build()
        self._center()
        # 坑 4：后台线程读 -> queue -> 主线程 pump（tkinter 不是线程安全的）
        self.root.after(100, self._pump)
        self.refresh()

    def _fit(self, center=False):
        """窗口尺寸贴内容（不小于最小宽度）；center=True 时居中。

        设备卡是异步建的（首扫约 5 秒），建窗那一刻内容还没就绪，
        所以数据到达后要再收紧一次；平时只改尺寸不挪位置（用户可能拖过窗口）。
        """
        self.root.update_idletasks()
        w = max(self.root.winfo_reqwidth(), int(MIN_PANEL_WIDTH * self._s))
        h = self.root.winfo_reqheight()
        if center:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 3)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
        else:
            self.root.geometry(f"{w}x{h}")

    def _center(self):
        """首次显示：按内容定尺寸并居中（小屏上尤其重要）。"""
        self._fit(center=True)

    # ---------------------------------------------------------- 构建界面
    def _build(self):
        T = self.T
        s = self._s
        wrap = tk.Frame(self.root, bg=T.BG, padx=int(20 * s), pady=int(18 * s))
        wrap.pack(fill="both", expand=True)

        # —— 顶部标题
        head = tk.Frame(wrap, bg=T.BG)
        head.pack(fill="x", pady=(0, int(14 * s)))
        tk.Label(head, text="无线外设电量", bg=T.BG, fg=T.FG,
                 font=T.font(15, "bold")).pack(side="left")
        self.stamp_lbl = tk.Label(head, text="正在检测 2.4G 设备…", bg=T.BG,
                                  fg=T.FG_FAINT, font=T.font(9))
        self.stamp_lbl.pack(side="right")

        # —— 设备卡片容器（卡片随检测结果动态增删）
        self.card_host = tk.Frame(wrap, bg=T.BG)
        self.card_host.pack(fill="both", expand=True)
        self._prime_card()

        # —— 底栏设置（分三组：启动 · 刷新 · 动作，组间留大间距）
        bar = T.Card(wrap)
        bar.pack(fill="x", pady=(int(4 * s), 0))
        opt = tk.Frame(bar.inner, bg=T.CARD, padx=int(16 * s), pady=int(12 * s))
        opt.pack(fill="x")

        # §一.5 / 验收 3：开关状态与任务管理器「启动」标签一致。
        # §四.7：启动时只 *读* 真实状态，不因为读到不一致就替用户改。
        self.autostart_toggle = T.Toggle(opt, initial=is_autostart_enabled(),
                                         command=self._on_autostart_toggle)
        self.autostart_toggle.pack(side="left")
        tk.Label(opt, text="开机自启", bg=T.CARD, fg=T.FG_DIM,
                 font=T.font(9)).pack(side="left", padx=(int(8 * s), 0))

        # 组分隔（视觉上断开两组设置）
        tk.Frame(opt, width=1, bg=T.BORDER).pack(side="left", fill="y",
                                                 padx=(int(16 * s), int(16 * s)),
                                                 pady=2)

        tk.Label(opt, text="自动刷新", bg=T.CARD, fg=T.FG_DIM,
                 font=T.font(9)).pack(side="left", padx=(0, int(9 * s)))
        iv_idx = min(2, max(0, self._interval_index()))
        self.interval_seg = T.Segmented(
            opt, ["30 秒", "1 分钟", "5 分钟"], index=iv_idx,
            on_change=self._on_interval_seg)
        self.interval_seg.pack(side="left")

        # 右侧动作按钮组（与左侧设置组用弹性空隙 + 分隔线断开）
        btn_sep = tk.Frame(opt, width=1, bg=T.BORDER)
        btn_sep.pack(side="right", fill="y", padx=(0, int(16 * s)), pady=2)
        T.flat_button(opt, "退出", self.quit, kind="ghost").pack(side="right",
                                                                 padx=(int(9 * s), 0))
        T.flat_button(opt, "重新检测", lambda: self.refresh(rescan=True),
                      kind="ghost").pack(side="right", padx=(int(9 * s), 0))
        T.flat_button(opt, "立即刷新", self.refresh, kind="primary").pack(side="right")

        self._building = False

    def _interval_index(self):
        """把当前 interval 秒数映射到 Segmented 的档位下标。"""
        if self.interval <= 30:
            return 0
        if self.interval <= 60:
            return 1
        return 2

    def _on_interval_seg(self, idx, text):
        n = int(text.split()[0])
        self.interval = n * 60 if "分钟" in text else n
        self.interval = max(MIN_INTERVAL, min(MAX_INTERVAL, self.interval))

    def _make_card(self, key, title):
        """为设备 key 建一张卡片，返回卡片控件字典。"""
        T = self.T
        s = self._s
        card = T.Card(self.card_host)
        card.pack(fill="x", pady=(0, int(10 * s)))
        body = card.inner
        inner = tk.Frame(body, bg=T.CARD, padx=int(18 * s), pady=int(16 * s))
        inner.pack(fill="both", expand=True)

        # 组件内部已按 DPI 缩放（ui_theme.S），这里传 96-DPI 基准尺寸即可
        ring = T.BatteryRing(inner, size=148, thickness=14)
        ring.grid(row=0, column=0, rowspan=2)

        right = tk.Frame(inner, bg=T.CARD)
        right.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(int(18 * s), 0))
        inner.columnconfigure(1, weight=1)

        tk.Label(right, text=title, bg=T.CARD, fg=T.FG,
                 font=T.font(13, "bold"), anchor="w").pack(fill="x")
        title_lbl = right.winfo_children()[-1]

        badge = tk.Label(right, text="读取中…", bg=T.CARD, fg=T.FG_DIM,
                         font=T.font(10), anchor="w")
        badge.pack(fill="x", pady=(int(3 * s), 0))

        T.hline(right).pack(fill="x", pady=(int(10 * s), int(9 * s)))

        # —— 指标：只留用户关心的「电池等级」；技术参数（固件/通道）降级成
        #    一行暗色小字，不再和用户信息抢视觉层级（用户反馈太拥挤）。
        grid = tk.Frame(right, bg=T.CARD)
        grid.pack(fill="x")
        m_level = T.Metric(grid, "电池等级")
        m_level.pack(side="left")
        tech = tk.Label(right, text="", bg=T.CARD, fg=T.FG_FAINT,
                        font=T.font(9), anchor="w")
        tech.pack(fill="x", pady=(int(7 * s), 0))

        # —— 电量趋势（面板开着期间的历史采样）
        # 组件内部已按 DPI 缩放，传 96-DPI 基准尺寸
        spark = T.Sparkline(right, width=210, height=38)
        spark.pack(fill="x", pady=(int(9 * s), 0))

        c = {"ring": ring, "badge": badge, "spark": spark, "name": title,
             "title": title_lbl, "widget": card,
             "m_level": m_level, "tech": tech}
        self.cards[key] = c
        return c

    def _prime_card(self):
        """检测完成前的占位卡（避免 5 秒全扫描期白屏）。"""
        self._make_card("_scan", "正在检测…")
        self.cards["_scan"]["ring"].set_color(self.T.COLOR_UNKNOWN, "--", "扫描 HID")
        self.cards["_scan"]["badge"].config(text="自动检测 2.4G 接收器与在线设备…")

    def _drop_card(self, key):
        c = self.cards.pop(key, None)
        if c:
            c["widget"].destroy()

    def _drop_prime(self):
        self._drop_card("_scan")

    def _on_autostart_toggle(self):
        """用户主动点开关时才写启动项 —— 程序自己绝不擅自改（§四.7）。"""
        want = self.autostart_toggle.state()
        have = is_autostart_enabled()
        if want == have:
            return
        if want:
            install_autostart(quiet=True)
        else:
            from shell import remove_shortcut
            remove_shortcut(autostart_lnk())
        real = is_autostart_enabled()
        if real != want:
            self.autostart_toggle.set(real)      # 写失败时回弹到真实状态
        self.stamp_lbl.config(text=f"开机自启已{'启用' if real else '关闭'}",
                              fg=self.T.FG_DIM)

    def _on_interval(self, _evt=None):
        txt = self.interval_var.get()
        n = int(txt.split()[0])
        self.interval = n * 60 if "分钟" in txt else n
        self.interval = max(MIN_INTERVAL, min(MAX_INTERVAL, self.interval))

    def refresh(self, rescan=False):
        # 互斥：并发访问同一 HID 设备会让响应错位（实测读到 0x0F/充电将满 这种垃圾，
        # 甚至 CEOF）。正在读就直接跳过，等这一轮结束。
        if self._busy:
            return
        self._busy = True
        self.stamp_lbl.config(text="读取中…", fg=self.T.FG_FAINT)
        threading.Thread(target=self._worker, args=(rescan,),
                         daemon=True).start()

    def _worker(self, rescan=False):
        try:
            self.result_q.put(read_all(rescan))
        except Exception as e:                     # noqa: BLE001
            self.result_q.put(e)

    def _pump(self):
        """主线程消费后台结果（坑 4）。"""
        try:
            while True:
                item = self.result_q.get_nowait()
                self._busy = False          # 本轮读完，放开互锁（先解锁再应用）
                self._apply(item)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _apply(self, rows):
        if isinstance(rows, Exception):
            self.stamp_lbl.config(text=f"读取异常: {rows}", fg=self.T.COLOR_LOW)
            return
        T = self.T
        self._drop_prime()
        if not rows:
            # 区分「接收器不在」和「设备休眠」：有接收器无在线设备 → 保留卡片置灰；
            # 连接收器都没有 → 全部卡片移除，只留提示。
            recv_present = bool(receivers_seen())
            if not recv_present:
                for key in list(self.cards):
                    self._drop_card(key)
                self.stamp_lbl.config(text=f"未检测到 2.4G 接收器 "
                                           f"{time.strftime('%H:%M:%S')}",
                                      fg=T.COLOR_MID)
                self.root.after(self.interval * 1000, self.refresh)
                return
            if not self.cards:
                c = self._make_card("_sleep", "2.4G 设备")
                c["ring"].set_color(T.COLOR_UNKNOWN, "--", "休眠")
            for c in self.cards.values():
                c["ring"].set_color(T.COLOR_UNKNOWN, "--", "休眠")
                c["badge"].config(text="● 设备未响应 — 检查开关 / 动一下鼠标",
                                  fg=T.COLOR_MID)
                c["m_level"].set("--", T.COLOR_UNKNOWN)
                c["tech"].config(text="")
            self.stamp_lbl.config(text=f"设备休眠 {time.strftime('%H:%M:%S')}",
                                  fg=T.COLOR_MID)
            self.root.after(self.interval * 1000, self.refresh)
            return

        seen = set()
        for r in rows:
            key = r["key"]
            seen.add(key)
            c = self.cards.get(key)
            if not c:
                c = self._make_card(key, r["name"])
            c["name"] = r["name"]
            pct, charging = r["percent"], r["charging"]
            color = T.pct_color(pct, charging)

            # 圆环（状态不再重复显示：环下 sub 只在休眠等特殊状态出现，
            # 常规放电/充电中由徽章行表达 —— 用户反馈信息重复拥挤）
            if pct is None:
                c["ring"].set_color(T.COLOR_UNKNOWN, r["level_text"][:4],
                                    r["state_text"])
            else:
                c["ring"].set(pct, sub="")
                if charging:
                    c["ring"].set_color(color, str(int(pct)), sub="")

            # 状态徽章
            badge_txt = r["state_text"]
            if charging:
                badge_txt = "⚡ " + badge_txt
            c["badge"].config(text=f"● {badge_txt}", fg=color)

            # 指标 + 技术参数降级行 + 趋势图
            c["m_level"].set(r["level_text"], color)
            c["tech"].config(text=f"固件 {r.get('firmware') or '--'}"
                                  f"  ·  0x1004 槽{r.get('slot', '?')}")
            c["spark"].push(pct, charging)
            if c["title"]["text"] != r["name"]:
                c["title"].config(text=r["name"])

        # 拔出/消失的设备 → 移除卡片；占位卡一并清掉
        for key in [k for k in self.cards
                    if k not in seen and k not in ("_scan", "_sleep")]:
            self._drop_card(key)

        self.stamp_lbl.config(text=f"更新于 {time.strftime('%H:%M:%S')}",
                              fg=T.FG_FAINT)
        self._fit()                      # 卡片建好/移除后收紧窗口（贴合内容）
        self.root.after(self.interval * 1000, self.refresh)

    def focus(self):
        """把已存在的面板唤到前台（§一.3）。"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.refresh()

    def quit(self):
        global _PANEL
        _PANEL = None
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.mainloop()


def open_panel_singleton():
    """打开面板；已开则前置，不重复开（§一.3）。"""
    global _PANEL
    if _PANEL is not None:
        try:
            _PANEL.focus()
            return _PANEL
        except tk.TclError:
            _PANEL = None
    _PANEL = Panel()
    # 托盘线程里调用时，Tk 必须跑在自己线程
    if threading.current_thread() is threading.main_thread():
        _PANEL.run()
    else:
        _PANEL.root.after(0, _PANEL.focus)
        threading.Thread(target=_PANEL.run, daemon=True).start()
    return _PANEL


def open_panel_and_wait():
    """在调用线程里跑面板（托盘通过线程调用本函数）。"""
    p = open_panel_singleton()
    return p


# ---------------------------------------------------------------- 形态 3: --debug
def cmd_debug():
    from hidpp import (list_interfaces, ReceiverBattery, discover_receivers,
                       scan_slots)
    from shell import desktop_dir, link_kind, DIR_LINK_NAME, FILE_LINK_NAME
    print("=" * 74)
    print("完整调试版")
    print("=" * 74)
    print("\n### 自动发现（无 PID 白名单，1.0 固件寄存器应答 = 接收器）")
    for vid, pid, sp, lp, fw in discover_receivers():
        n_if = len(list_interfaces(vid, pid))
        print(f"\n  接收器 {vid:04X}:{pid:04X}  固件 {fw}  "
              f"HID 接口 {n_if} 个")
        print(f"  col01(短报文 7B) = {'有' if sp else '无'}")
        print(f"  col02(长报文 20B) = {'有' if lp else '无'}")
        b = ReceiverBattery()
        if not b.open_paths(sp, lp):
            print(f"  打开失败: {b.last_error}")
            continue
        for slot, resp in scan_slots(b.long).items():
            print(f"  槽位 {slot}: {'在线' if resp is not None else '—'}")
            if resp is None:
                continue
            b.slot = slot
            idx = b.resolve_battery_feature()
            print(f"    设备名: {b.read_device_name() or '未知'}")
            print(f"    UNIFIED_BATTERY(0x1004) feature_index = "
                  f"{('0x%02X' % idx) if idx else '未实现'}")
            data = b.read_battery()
            if data:
                print(f"    电量: {data['percent']}%  状态: {data['state_text']}  "
                      f"充电: {data['charging']}")
                print(f"    原始响应: {data['raw']}")
            else:
                print(f"    读电量失败: {b.last_error}")
        b.close()

    devs = discover_devices()
    print(f"\n### 汇总：{len(receivers_seen())} 台接收器，"
          f"{len(devs)} 台在线设备")
    for d in devs:
        print(f"  {d['name']}  {d['vid']:04X}:{d['pid']:04X} 槽位 {d['slot']}")

    print("\n### 安装状态")
    print(f"  开机自启: {'已启用' if is_autostart_enabled() else '未启用'}")
    print(f"    路径: {autostart_lnk()}")
    from shell import (desktop_dir, resolve_link_display, DIR_LINK_NAME,
                       FILE_LINK_NAME)
    for label, name in (("桌面目录链接", DIR_LINK_NAME),
                        ("桌面文件链接", FILE_LINK_NAME)):
        kind, real = resolve_link_display(os.path.join(desktop_dir(), name))
        print(f"  {label}: {kind or '缺失'}  {real}")
    return 0


# ---------------------------------------------------------------- 开机自启 / 桌面链接
def startup_folder():
    return os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup")


def autostart_lnk():
    return os.path.join(startup_folder(), f"{APP_NAME}.lnk")


def install_autostart(quiet=False):
    """在「启动」文件夹写快捷方式（目标 pythonw.exe + 脚本，绝不弹黑窗）。"""
    from shell import create_shortcut, autostart_target
    pythonw, script = autostart_target(HERE)
    lnk = autostart_lnk()
    # start_tray.pyw 自己默认走 --tray，这里不再重复传参
    create_shortcut(lnk, pythonw, f'"{script}"', HERE,
                    desc="开机自启：无线外设电量托盘")
    if not quiet:
        print(f"已写入启动项: {lnk}")
        print(f"  目标: {pythonw} \"{script}\"")
    return 0


def is_autostart_enabled():
    """§四.7：只 *读* 真实状态（启动文件夹里有没有那个 .lnk）。"""
    return os.path.exists(autostart_lnk())


# ---------------------------------------------------------------- 入口
def main(argv=None):
    global DEBUG
    ap = argparse.ArgumentParser(description=APP_TITLE)
    ap.add_argument("--once", action="store_true", help="打印一次电量并退出")
    ap.add_argument("--tray", action="store_true", help="托盘常驻")
    ap.add_argument("--debug", action="store_true", help="完整调试版")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                    help=f"刷新间隔秒（{MIN_INTERVAL}~{MAX_INTERVAL}）")
    ap.add_argument("--install-autostart", action="store_true", help="安装开机自启")
    ap.add_argument("--remove-autostart", "--uninstall-autostart",
                    action="store_true", dest="remove_autostart",
                    help="移除开机自启")
    ap.add_argument("--make-shortcuts", action="store_true", help="创建桌面链接")
    ap.add_argument("--remove-shortcuts", action="store_true", help="移除桌面链接")
    args = ap.parse_args(argv)
    DEBUG = args.debug

    if args.once:
        return cmd_once()
    if args.debug:
        return cmd_debug()
    if args.tray:
        from tray import run_tray
        return run_tray(args.interval)
    if args.install_autostart:
        return install_autostart()
    if args.remove_autostart:
        from shell import remove_shortcut
        remove_shortcut(autostart_lnk())
        print("已移除开机自启")
        return 0
    if args.make_shortcuts:
        from shell import make_desktop_links
        return make_desktop_links(HERE)
    if args.remove_shortcuts:
        from shell import remove_desktop_links
        remove_desktop_links()
        return 0

    open_panel_singleton()
    return 0


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    sys.exit(main())