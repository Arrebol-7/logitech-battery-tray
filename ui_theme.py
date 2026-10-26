"""面板视觉主题 + 圆环电量表绘制（纯 tkinter Canvas，无第三方库）。

设计目标：把原来朴素的 ttk 灰控件换成深色卡片 + 彩色圆环，接近手机电池 App。
1280x720 小屏也能完整显示。

配色（深色）:
    背景 #16181D   卡片 #1F2229   描边 #2B2F38
    主文字 #F2F4F8  次文字 #8B93A3
    绿 #3DDC84（≥60） 黄 #FFC24B（30-59） 红 #FF6B6B（<30） 灰 #6B7280（未知）
"""
from __future__ import annotations

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
        self._pct_text = self.create_text(cx, cy - 2, text="--",
                                          fill=FG, font=font(int(s * 0.26), "bold"))
        self._unit_text = self.create_text(cx + s * 0.215, cy + s * 0.055,
                                           text="%", fill=FG_DIM,
                                           font=font(int(s * 0.093)))
        self._sub_text = self.create_text(cx, cy + s * 0.2, text="",
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