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
from tkinter import ttk

APP_NAME = "电量托盘"
APP_TITLE = "无线外设电量"
HERE = os.path.dirname(os.path.abspath(__file__))
STARTUP_SCRIPT = os.path.join(HERE, "start_tray.pyw")
DEFAULT_INTERVAL = 60
MIN_INTERVAL, MAX_INTERVAL = 30, 1800

DEVICES = [
    {"key": "gpw2", "name": "GPW2 无线鼠标", "vid": 0x046D, "pid": 0xC547},
]

DEBUG = False


def log(msg):
    """§一.7：只在 --debug 时输出，不写任何日志文件。"""
    if DEBUG:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- 读取
def read_one(dev):
    """读一台设备的电量，返回 dict 或 None。"""
    from hidpp import Gpw2Battery
    b = Gpw2Battery(dev["vid"], dev["pid"])
    try:
        if not b.open():
            log(f"{dev['name']}: 打开失败 {b.last_error}")
            return None
        data = b.read_battery()
        if data is None:
            log(f"{dev['name']}: {b.last_error}")
            return None
        fw = b.read_firmware()
        conn = b.read_connection_count()
        data.update({"key": dev["key"], "name": dev["name"],
                     "firmware": fw, "connected": conn})
        log(f"{dev['name']}: {data['percent']}% {data['state_text']} "
            f"level={data['level_text']} raw={data['raw']}")
        return data
    finally:
        b.close()


def read_all():
    return [d for d in (read_one(x) for x in DEVICES) if d]


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
        # 区分两种失败：接收器不在 / 设备休眠
        from hidpp import Gpw2Battery
        b = Gpw2Battery()
        if not b.open():
            print(f"未找到接收器（VID:PID = {DEVICES[0]['vid']:04X}:"
                  f"{DEVICES[0]['pid']:04X}）—— 请确认已插入")
            print(f"  详情: {b.last_error}")
        else:
            fw = b.read_firmware()
            print(f"接收器在线（固件 {fw}），但设备无响应。")
            print("  → 请动一下鼠标唤醒后再试。")
            b.close()
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
        底栏    开机自启 · 刷新间隔 · 立即刷新 · 退出
    """

    def __init__(self):
        import ui_theme as T
        self.T = T
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.resizable(False, False)
        self.root.configure(bg=T.BG)
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

    def _center(self):
        """居中显示（小屏上尤其重要）。"""
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 3)
        self.root.geometry(f"+{x}+{y}")

    # ---------------------------------------------------------- 构建界面
    def _build(self):
        T = self.T
        wrap = tk.Frame(self.root, bg=T.BG, padx=18, pady=16)
        wrap.pack(fill="both", expand=True)

        # —— 顶部标题
        head = tk.Frame(wrap, bg=T.BG)
        head.pack(fill="x", pady=(0, 12))
        tk.Label(head, text="无线外设电量", bg=T.BG, fg=T.FG,
                 font=T.font(14, "bold")).pack(side="left")
        self.stamp_lbl = tk.Label(head, text="尚未更新", bg=T.BG, fg=T.FG_FAINT,
                                  font=T.font(9))
        self.stamp_lbl.pack(side="right")

        # —— 每台设备一张卡片
        for dev in DEVICES:
            card = T.Card(wrap)
            card.pack(fill="x", pady=(0, 10))
            body = card.inner
            inner = tk.Frame(body, bg=T.CARD, padx=16, pady=14)
            inner.pack(fill="both", expand=True)

            # 左：圆环
            ring = T.BatteryRing(inner, size=132, thickness=13)
            ring.grid(row=0, column=0, rowspan=2)

            # 右：设备名 + 状态徽章 + 明细
            right = tk.Frame(inner, bg=T.CARD)
            right.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(16, 0))
            inner.columnconfigure(1, weight=1)

            tk.Label(right, text=dev["name"], bg=T.CARD, fg=T.FG,
                     font=T.font(12, "bold"), anchor="w").pack(fill="x")

            badge = tk.Label(right, text="读取中…", bg=T.CARD, fg=T.FG_DIM,
                             font=T.font(9), anchor="w")
            badge.pack(fill="x", pady=(3, 0))

            T.hline(right).pack(fill="x", pady=(9, 9))

            detail = tk.Label(right, text="数据来源 —", bg=T.CARD, fg=T.FG_DIM,
                              font=T.font(8), anchor="w", justify="left",
                              wraplength=190)
            detail.pack(fill="x")

            self.cards[dev["key"]] = {"ring": ring, "badge": badge,
                                      "detail": detail, "name": dev["name"]}

        # —— 底栏设置
        bar = T.Card(wrap)
        bar.pack(fill="x", pady=(2, 0))
        opt = tk.Frame(bar.inner, bg=T.CARD, padx=14, pady=10)
        opt.pack(fill="x")

        # §一.5 / 验收 3：勾选框状态与任务管理器「启动」标签一致。
        # §四.7：启动时只 *读* 真实状态，不因为读到不一致就替用户改。
        self.autostart_var = tk.BooleanVar(value=is_autostart_enabled())
        self.autostart_cb = tk.Checkbutton(
            opt, text="开机自启", variable=self.autostart_var,
            command=self._on_autostart_click, bg=T.CARD, fg=T.FG,
            activebackground=T.CARD, activeforeground=T.FG,
            selectcolor=T.CARD_HI, font=T.font(9), bd=0,
            highlightthickness=0, cursor="hand2")
        self.autostart_cb.pack(side="left")

        tk.Label(opt, text="每", bg=T.CARD, fg=T.FG_DIM,
                 font=T.font(9)).pack(side="left", padx=(18, 5))
        self.interval_var = tk.StringVar(
            value=f"{self.interval} 秒" if self.interval < 60
            else f"{self.interval // 60} 分钟")
        iv = ttk.Combobox(opt, textvariable=self.interval_var, width=7,
                          state="readonly", font=T.font(9),
                          values=["30 秒", "1 分钟", "2 分钟", "5 分钟",
                                  "10 分钟", "30 分钟"])
        iv.pack(side="left")
        iv.bind("<<ComboboxSelected>>", self._on_interval)
        tk.Label(opt, text="自动刷新", bg=T.CARD, fg=T.FG_DIM,
                 font=T.font(9)).pack(side="left", padx=(5, 0))

        T.flat_button(opt, "退出", self.quit, kind="ghost").pack(side="right", padx=(6, 0))
        T.flat_button(opt, "立即刷新", self.refresh, kind="primary").pack(side="right")

        self._building = False

    def _on_autostart_click(self):
        """用户主动点勾选框时才写启动项 —— 程序自己绝不擅自改（§四.7）。"""
        want = self.autostart_var.get()
        have = is_autostart_enabled()
        if want == have:
            return
        if want:
            install_autostart(quiet=True)
        else:
            from shell import remove_shortcut
            remove_shortcut(autostart_lnk())
        real = is_autostart_enabled()
        self.autostart_var.set(real)
        self.stamp_lbl.config(text=f"开机自启已{'启用' if real else '关闭'}",
                              fg=self.T.FG_DIM)

    def _on_interval(self, _evt=None):
        txt = self.interval_var.get()
        n = int(txt.split()[0])
        self.interval = n * 60 if "分钟" in txt else n
        self.interval = max(MIN_INTERVAL, min(MAX_INTERVAL, self.interval))

    def refresh(self):
        # 互斥：并发访问同一 HID 设备会让响应错位（实测读到 0x0F/充电将满 这种垃圾，
        # 甚至 CEOF）。正在读就直接跳过，等这一轮结束。
        if self._busy:
            return
        self._busy = True
        self.stamp_lbl.config(text="读取中…", fg=self.T.FG_FAINT)
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            self.result_q.put(read_all())
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
        if not rows:
            for c in self.cards.values():
                c["ring"].set_color(T.COLOR_UNKNOWN, "--", "无信号")
                c["badge"].config(text="未读到设备 — 检查接收器 / 动一下鼠标",
                                  fg=T.COLOR_MID)
                c["detail"].config(text="设备休眠时不响应请求，唤醒后会自动恢复。")
            self.stamp_lbl.config(text=f"读取失败 {time.strftime('%H:%M:%S')}",
                                  fg=T.COLOR_MID)
            self.root.after(self.interval * 1000, self.refresh)
            return

        for r in rows:
            c = self.cards.get(r["key"])
            if not c:
                continue
            pct, charging = r["percent"], r["charging"]
            color = T.pct_color(pct, charging)

            # 圆环
            if pct is None:
                c["ring"].set_color(T.COLOR_UNKNOWN, r["level_text"][:4],
                                    r["state_text"])
            else:
                c["ring"].set(pct, r["state_text"])
                if charging:
                    c["ring"].set_color(color, str(int(pct)), r["state_text"])

            # 状态徽章
            badge_txt = r["state_text"]
            if charging:
                badge_txt = "⚡ " + badge_txt
            c["badge"].config(text=f"● {badge_txt}", fg=color)

            # 明细：说得像人话（"已连接设备 0" 会被误读成"没连上"，实际是
            # 接收器上报的通道计数，对 GPW2 单设备场景无意义）。
            lines = [f"读取方式  HID++ 2.0 特性 0x1004（UNIFIED_BATTERY）",
                     f"电池等级  {r['level_text']}"]
            if r.get("firmware"):
                lines.append(f"接收器固件  {r['firmware']}")
            c["detail"].config(text="\n".join(lines))

        self.stamp_lbl.config(text=f"更新于 {time.strftime('%H:%M:%S')}",
                              fg=T.FG_FAINT)
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
    from hidpp import list_interfaces, find_channels, Gpw2Battery
    from shell import desktop_dir, link_kind, DIR_LINK_NAME, FILE_LINK_NAME
    print("=" * 74)
    print("完整调试版")
    print("=" * 74)
    for dev in DEVICES:
        print(f"\n### {dev['name']}  {dev['vid']:04X}:{dev['pid']:04X}")
        paths = list_interfaces(dev["vid"], dev["pid"])
        print(f"  HID 接口数: {len(paths)}")
        for p in paths:
            tag = p.split("#")[-1] if "#" in p else p
            print(f"    - ...{tag[:60]}")
        sp, lp = find_channels(dev["vid"], dev["pid"])
        print(f"  col01(短报文 7B) = {'有' if sp else '无'}")
        print(f"  col02(长报文 20B) = {'有' if lp else '无'}")

        b = Gpw2Battery(dev["vid"], dev["pid"])
        if not b.open():
            print(f"  打开失败: {b.last_error}")
            continue
        print(f"  接收器固件: {b.read_firmware()}")
        print(f"  已连接设备数: {b.read_connection_count()}")
        idx = b.resolve_battery_feature()
        print(f"  UNIFIED_BATTERY(0x1004) feature_index = "
              f"{('0x%02X' % idx) if idx else '未实现'}")
        data = b.read_battery()
        if data:
            print(f"  电量: {data['percent']}%  状态: {data['state_text']}  "
                  f"充电: {data['charging']}")
            print(f"  原始响应: {data['raw']}")
        else:
            print(f"  读电量失败: {b.last_error}")
        b.close()

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