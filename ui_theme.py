"""面板视觉主题 + 圆环电量表绘制（纯 tkinter Canvas，无第三方库）。

设计目标：把原来朴素的 ttk 灰控件换成深色卡片 + 彩色圆环，接近手机电池 App。
1280x720 小屏也能完整显示。

配色（深色）:
    背景 #16181D   卡片 #1F2229   描边 #2B2F38
    主文字 #F2F4F8  次文字 #8B93A3
    绿 #3DDC84（≥60） 黄 #FFC24B（30-59） 红 #FF6B6B（<30） 灰 #6B7280（未知）
"""
from __future__ import annotations

import math

import tkinter as tk

# ---- 配色 ----------------------------------------------------------------
BG = "#16181D"
CARD = "#1F2229"
CARD_HI = "#242833"
BORDER = "#2B2F38"
TRACK = "#2E333D"          # 圆环底槽
FG = "#F2F4F8"
FG_DIM = "#9BA3B4"          # 次要文字（原来 #8B93A3 在高 DPI 小字号下偏暗）
FG_FAINT = "#6B7280"
ACCENT = "#4C8DFF"

COLOR_OK = "#3DDC84"
COLOR_MID = "#FFC24B"
COLOR_LOW = "#FF6B6B"
COLOR_UNKNOWN = "#6B7280"
COLOR_CHARGING = "#4CC9F0"

FONT = "Microsoft YaHei UI"
FONT_FALLBACK = "Segoe UI"


def pct_color(pct, charging=False):
    """电量色阶：充电中统一用青色；否则 ≥60 绿 / 30-59 黄 / <30 红。"""
    if charging:
        return COLOR_CHARGING
    if pct is None:
        return COLOR_UNKNOWN
    if pct >= 60:
        return COLOR_OK
    if pct >= 30:
        return COLOR_MID
    return COLOR_LOW


def font(size, weight="normal"):
    return (FONT, size, weight)


# ---------------------------------------------------------------- 圆环
class BatteryRing(tk.Canvas):
    """圆环电量表：底槽 + 彩色进度弧 + 中心大号百分比。

    1280x720 屏幕下面板整体控制在 ~400x360 内。
    """

    def __init__(self, master, size=132, thickness=13, bg=CARD):
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0)
        self.size = size
        self.thickness = thickness
        self._arc = None
        self._pct_text = None
        self._unit_text = None
        self._sub_text = None
        self._draw_static()

    def _draw_static(self):
        s, t = self.size, self.thickness
        pad = t / 2 + 2
        box = (pad, pad, s - pad, s - pad)
        # 底槽（整圈）
        self.create_oval(*box, outline=TRACK, width=t, tags="track")
        # 进度弧：12 点方向起，屏幕上顺时针填充。
        # ⚠ 实测（find_overlapping 探点）：start=90 是 12 点，extent 取【负】才落在
        #   3 点钟侧（屏幕顺时针）；取正会跑到 9 点钟侧（逆时针）。别凭直觉改。
        self._arc = self.create_arc(*box, start=90, extent=0, style="arc",
                                    outline=COLOR_UNKNOWN, width=t)
        cx = cy = s / 2
        # 布局留白（用户反馈「太拥挤」）：数字整体上移，% 紧贴数字右侧而非环缘，
        # 状态字下移到数字与环内缘之间留出呼吸空间。
        self._pct_text = self.create_text(cx - s * 0.03, cy - s * 0.045,
                                          text="--", fill=FG,
                                          font=font(int(s * 0.25), "bold"))
        self._unit_text = self.create_text(cx + s * 0.17, cy + s * 0.005,
                                           text="%", fill=FG_DIM,
                                           font=font(int(s * 0.085)))
        self._sub_text = self.create_text(cx, cy + s * 0.215, text="",
                                          fill=FG_DIM, font=font(9))

    def set(self, pct, sub=""):
        color = pct_color(pct)
        extent = -max(0.0, min(100.0, pct or 0)) * 3.6   # 负数 = 屏幕顺时针（实测）
        self.itemconfig(self._arc, extent=extent, outline=color)
        self.itemconfig(self._pct_text,
                        text="--" if pct is None else str(int(pct)),
                        fill=color if pct is not None else FG_DIM)
        self.itemconfig(self._sub_text, text=sub, fill=FG_DIM)

    def set_color(self, color, pct_text="--", sub=""):
        """充电中/特殊状态用自定义颜色。"""
        self.itemconfig(self._arc, outline=color)
        self.itemconfig(self._pct_text, text=pct_text, fill=color)
        self.itemconfig(self._sub_text, text=sub, fill=FG_DIM)


# ---------------------------------------------------------------- 小部件
def hline(parent, color=BORDER):
    f = tk.Frame(parent, height=1, bg=color)
    return f


class Card(tk.Frame):
    """深色圆角观感卡片（tkinter 无真圆角，用 1px 描边模拟）。"""

    def __init__(self, master, **kw):
        super().__init__(master, bg=BORDER, **kw)          # 外层当描边
        self.inner = tk.Frame(self, bg=CARD)
        self.inner.pack(fill="both", expand=True, padx=1, pady=1)


def flat_button(parent, text, command, kind="normal", width=None):
    """自绘扁平按钮（ttk 按钮在深色下太丑）。"""
    if kind == "primary":
        bg, fg, hover = ACCENT, "#FFFFFF", "#5E9AFF"
    elif kind == "ghost":
        bg, fg, hover = CARD_HI, FG_DIM, "#30353F"
    else:
        bg, fg, hover = CARD_HI, FG, "#30353F"
    lbl = tk.Label(parent, text=text, bg=bg, fg=fg, font=font(9),
                   padx=14, pady=6, cursor="hand2")
    if width:
        lbl.config(width=width)
    lbl.bind("<Button-1>", lambda _e: command())
    lbl.bind("<Enter>", lambda _e: lbl.config(bg=hover))
    lbl.bind("<Leave>", lambda _e: lbl.config(bg=bg))
    return lbl


# ================================================================= 组件库
# 下面是本项目自建的深色组件：Toggle 开关、Segmented 分段选择器、
# Metric 指标块、Sparkline 迷你趋势图。全部纯 tkinter 自绘，
# 替换原生 ttk 控件（Checkbutton/Combobox 与深色卡片完全不搭）。

class Toggle(tk.Canvas):
    """iOS 风格开关（替代原生 Checkbutton）。state() 可读当前布尔值。"""

    def __init__(self, master, initial=False, command=None, width=40, height=22):
        super().__init__(master, width=width, height=height, bg=master["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.w, self.h = width, height
        self.on = initial
        self.command = command
        self._track = self.create_oval(1, 1, width - 1, height - 1,
                                       fill=CARD_HI, outline="")
        r = (height - 6) / 2
        cy = height / 2
        self._knob = self.create_oval(4, cy - r, 4 + 2 * r, cy + r,
                                      fill=FG_DIM, outline="")
        self.bind("<Button-1>", self._click)
        self.set(initial)                 # ⚠ initial 状态必须真正画出来

    def _click(self, _e=None):
        self.set(not self.on)
        if self.command:
            self.command()

    def set(self, on):
        self.on = bool(on)
        fill = COLOR_OK if self.on else CARD_HI
        knob_fill = "#FFFFFF" if self.on else FG_DIM
        self.itemconfig(self._track, fill=fill)
        self.itemconfig(self._knob, fill=knob_fill)
        if self.on:
            self.move(self._knob, (self.w - 8) - self.coords(self._knob)[0], 0)
        else:
            self.move(self._knob, 4 - self.coords(self._knob)[0], 0)

    def state(self):
        return self.on


class Segmented(tk.Canvas):
    """分段选择器（替代 ttk.Combobox）：一排互斥选项，选中的亮起。

    on_change(索引, 值) 在切换时回调。段间留 8px 间隙（用户反馈太拥挤），
    选中态 = 亮底 + 亮字；未选中 = 透明底 + 暗字。
    """

    def __init__(self, master, options, index=1, on_change=None, gap=8):
        self.opts = list(options)
        self.idx = index
        self.on_change = on_change
        bg = master["bg"]
        # 预估宽度：中文字符约 12px/字 + 左右内边距
        wpl = [sum(11 if ord(ch) > 0x2E7F else 7 for ch in o) + 16
               for o in self.opts]
        total = sum(wpl) + gap * (len(self.opts) - 1) + 4
        super().__init__(master, width=total, height=26, bg=bg,
                         highlightthickness=0, bd=0)
        self._segs = []
        x = 2
        for i, (o, wd) in enumerate(zip(self.opts, wpl)):
            s = self.create_rectangle(x, 3, x + wd, 22, fill=bg, outline="",
                                      tags=f"s{i}")
            t = self.create_text(x + wd / 2, 13, text=o, fill=FG_FAINT,
                                 font=font(9), tags=f"s{i}")
            self._segs.append((s, t, x, x + wd))
            self.tag_bind(f"s{i}", "<Button-1>",
                          lambda _e, k=i: self._pick(k))
            x += wd + gap
        self._paint()

    def _paint(self):
        for i, (s, t, _x0, _x1) in enumerate(self._segs):
            if i == self.idx:
                self.itemconfig(s, fill=CARD_HI)
                self.itemconfig(t, fill=FG)
            else:
                self.itemconfig(s, fill=self.master["bg"])
                self.itemconfig(t, fill=FG_FAINT)

    def _pick(self, k):
        if k == self.idx:
            return
        self.idx = k
        self._paint()
        if self.on_change:
            self.on_change(k, self.opts[k])

    def set(self, index):
        self._pick(index) if index != self.idx else None


class Metric(tk.Frame):
    """指标块：小标签 + 数值 + 单位（卡片右侧信息网格用）。"""

    def __init__(self, master, label, value="--", unit="", color=None):
        super().__init__(master, bg=CARD)
        tk.Label(self, text=label, bg=CARD, fg=FG_FAINT, font=font(8),
                 anchor="w").pack(fill="x")
        row = tk.Frame(self, bg=CARD)
        row.pack(fill="x")
        self.val = tk.Label(row, text=value, bg=CARD, fg=color or FG,
                            font=font(12, "bold"), anchor="w")
        self.val.pack(side="left")
        if unit:
            tk.Label(row, text=unit, bg=CARD, fg=FG_FAINT,
                     font=font(8)).pack(side="left", padx=(3, 0), pady=(4, 0))

    def set(self, value, color=None, unit=None):
        self.val.config(text=value, fg=color or FG)


class Sparkline(tk.Canvas):
    """迷你电量历史趋势图（最近 N 个采样点，当前色渲染）。"""

    def __init__(self, master, width=190, height=34, keep=40):
        super().__init__(master, width=width, height=height, bg=CARD,
                         highlightthickness=0, bd=0)
        self.w, self.h = width, height
        self.keep = keep
        self.pts = []                     # [(pct, charging)]
        self._line = None
        self._area = None

    def push(self, pct, charging=False):
        """新增一个采样点并重绘。pct 为 None 跳过。"""
        if pct is None:
            return
        self.pts.append((float(pct), charging))
        if len(self.pts) > self.keep:
            self.pts = self.pts[-self.keep:]
        self._draw()

    def _draw(self):
        for iid in self.find_all():
            self.delete(iid)
        if len(self.pts) < 2:
            tk_lbl = "暂无趋势数据"
            self.create_text(self.w / 2, self.h / 2, text=tk_lbl,
                             fill=FG_FAINT, font=font(8))
            return
        lo, hi = 0, 100
        n = len(self.pts)
        step = self.w / max(n - 1, 1)
        coords = []
        for i, (p, _c) in enumerate(self.pts):
            x = i * step
            y = self.h - 3 - (p - lo) / (hi - lo) * (self.h - 8)
            coords += [x, y]
        color = pct_color(self.pts[-1][0], self.pts[-1][1])
        self._area = self.create_polygon(
            coords + [self.w, self.h, 0, self.h],
            fill=color, stipple="gray25", outline="")
        self._line = self.create_line(*coords, fill=color, width=2,
                                      smooth=True)
        # 末点圆点
        self.create_oval(coords[-2] - 2.5, coords[-1] - 2.5,
                         coords[-2] + 2.5, coords[-1] + 2.5,
                         fill=color, outline="")