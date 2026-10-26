"""托盘常驻（纯 ctypes，无 pystray/PIL）。

PROMPT.md 逐条对应:
  §一.3 右键 → 打开/前置简洁面板（已开则不重复开）；左键 → 立刻刷新；
        悬停 tooltip = "设备名：电量（更新时间）"
  §一.4 低 CPU：SetTimer 驱动刷新，空闲不轮询、不 sleep 循环
  §一.8 关闭时进程真正退出，不留僵尸进程
  §四.3 WM_DESTROY 里必须 PostQuitMessage(0)
  §四.5 图标不"删了再加"（NIM_DELETE 删掉的用 NIM_MODIFY 改不回来）；
        要 SetProcessDPIAware，否则图标被系统缩放发糊
  §四.7 常驻程序只 *读* 开机自启的真实状态，绝不替用户建/删启动项

单实例：用一个命名互斥体（CreateMutexW）保证托盘只有一个。
第二个实例启动时，把已有实例的面板唤到前台（通过 WM_COPYDATA 通知）。

图标：用 GDI 在内存位图上画电量数字，转成 HICON。
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import POINTER, WINFUNCTYPE, byref, c_void_p, c_wchar_p, wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)   # Shell_NotifyIconW 在这里

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_NULL = 0x0000
WM_COPYDATA = 0x004A
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_TIMER = 0x0113

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
MF_STRING, MF_SEPARATOR, MF_GRAYED = 0x0000, 0x0800, 0x0001
MF_CHECKED = 0x0008
TPM_RIGHTBUTTON = 0x0002
TPM_BOTTOMALIGN = 0x0020
IDM_REFRESH, IDM_PANEL, IDM_AUTOSTART, IDM_EXIT = 1001, 1002, 1003, 1004

MUTEX_NAME = "Global\\HermesBatteryTray_SingleInstance"
WINDOW_CLASS = "HermesBatteryTrayWnd"

HERE = os.path.dirname(os.path.abspath(__file__))


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                             ctypes.c_ulonglong, ctypes.c_longlong)
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  ctypes.c_ulonglong, ctypes.c_longlong]
user32.DefWindowProcW.restype = ctypes.c_longlong
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(wintypes.RECT), wintypes.UINT]

# --- 以下原型是必须的：64 位下 ctypes 默认把返回值当 c_int，
#     句柄（HBITMAP/HDC/HICON 都是 64 位指针）会被截断成垃圾 → 访问违规崩溃。
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT,
                               wintypes.UINT, ctypes.c_void_p]
gdi32.CreateBitmap.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p),
                                   wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 13 + [wintypes.LPCWSTR]
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
gdi32.SetTextColor.restype = wintypes.DWORD
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                            wintypes.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, wintypes.HDC,
                         ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                            wintypes.HBRUSH]
user32.FillRect.restype = ctypes.c_int
user32.DrawTextW.restype = ctypes.c_int
user32.CreateIconIndirect.argtypes = [ctypes.c_void_p]
user32.CreateIconIndirect.restype = wintypes.HICON
user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.c_void_p]
user32.GetIconInfo.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [("dwData", ctypes.c_void_p), ("cbData", wintypes.DWORD),
                ("lpData", ctypes.c_void_p)]


# ---------------------------------------------------------------- DPI
def set_dpi_aware():
    """§四.5：不声明 DPI 感知的话，系统会把图标缩放出糊边。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)     # PER_MONITOR_AWARE_V2
    except Exception:                                       # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:                                   # noqa: BLE001
            pass


# ---------------------------------------------------------------- 图标绘制
def _get_icon_size():
    """按系统 DPI 选图标边长（§四.5：高 DPI 下 16px 会发糊）。"""
    try:
        sm = user32.GetSystemMetrics(49)        # SM_CXSMICON
        if sm and 16 <= sm <= 64:
            return sm
    except Exception:                           # noqa: BLE001
        pass
    return 16


# 4x6 点阵数字（托盘图标中心用）。
# ⚠ 别用 3x5：2 和 5 在 3x5 下只差第 2/4 行，肉眼与 OCR 都会混（实测被读成 20）。
_DIGITS = {
    '0': ("0110", "1001", "1001", "1001", "1001", "0110"),
    '1': ("0010", "0110", "0010", "0010", "0010", "0111"),
    '2': ("0110", "1001", "0001", "0010", "0100", "1111"),
    '3': ("1110", "0001", "0110", "0001", "0001", "1110"),
    '4': ("0010", "0110", "1010", "1010", "1111", "0010"),
    '5': ("1111", "1000", "1110", "0001", "0001", "1110"),
    '6': ("0110", "1000", "1110", "1001", "1001", "0110"),
    '7': ("1111", "0001", "0010", "0010", "0100", "0100"),
    '8': ("0110", "1001", "0110", "1001", "1001", "0110"),
    '9': ("0110", "1001", "1001", "0111", "0001", "0110"),
    '-': ("0000", "0000", "0110", "0000", "0000", "0000"),
}


class ICONINFO(ctypes.Structure):
    """正确的 ICONINFO 布局（原来用裸 buffer + memmove 靠运气，hotspot 未清零）。"""
    _fields_ = [("fIcon", wintypes.BOOL),
                ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


def make_icon(percent, charging=False, size=None):
    """用 GDI 画电量圆环图标（环 + 中心数字），返回 HICON。

    视觉与面板同色阶：≥60 绿 / 30–59 黄 / <30 红；充电中青色。

    实现要点（都是踩过的坑）:
      * 用 32 位带 alpha 的 DIB 当颜色位图，才能有真正的平滑透明边；
      * ICONINFO 必须用结构体，不能用裸 buffer + memmove（hotspot 会残留垃圾）；
      * mask 位图对 alpha 图标可全 0（表示"按颜色位图的 alpha 走"）。
    """
    if size is None:
        size = _get_icon_size()

    hdc_screen = user32.GetDC(None)
    hdc = gdi32.CreateCompatibleDC(hdc_screen)

    # —— 32 位 DIB（自上而下，BGRA）
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = size
    bi.bmiHeader.biHeight = -size
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0          # BI_RGB
    bits = c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc_screen, byref(bi), 0, byref(bits), None, 0)
    old = gdi32.SelectObject(hdc, hbmp)

    # 全透明起步（DIB 初始为 0，alpha=0）
    ctypes.memset(bits, 0, size * size * 4)

    # 取色（BGR 序，GDI 用）
    if charging:
        color = 0x00F0C94C          # 青 #4CC9F0
    elif percent is None:
        color = 0x00807A6B
    elif percent >= 60:
        color = 0x0084DC3D          # 绿 #3DDC84
    elif percent >= 30:
        color = 0x004BC2FF          # 黄 #FFC24B
    else:
        color = 0x006B6BFF          # 红 #FF6B6B
    rgb = ((color & 0xFF) << 16) | (color & 0xFF00) | ((color >> 16) & 0xFF)

    pad = max(1, size // 10)
    thick = max(2, size // 6)
    cx = cy = size / 2.0
    r = (size - 2 * pad - thick) / 2.0

    # GDI 画在 DIB 上 alpha 恒为 0（不可见），所以这里手工写像素。
    # ⚠ 必须用 bits.value（数据地址），不能用 addressof(bits)（那是栈上指针变量的地址，
    #   往那儿 memmove 会踩坏栈，偏移一大就段错误 —— 实测踩过）。
    base = bits.value

    def put(x, y, rgb_col, a=255):
        if 0 <= x < size and 0 <= y < size:
            off = (y * size + x) * 4
            b = rgb_col & 0xFF
            gg = (rgb_col >> 8) & 0xFF
            rr = (rgb_col >> 16) & 0xFF
            ctypes.memmove(base + off, bytes((b, gg, rr, a)), 4)

    def blend_ring(radius, width, rgb_col, a=255, start_deg=0.0, span_deg=360.0):
        """在半径 radius 处画一段圆环（按角度范围）。用超采样抗锯齿。"""
        import math
        inner = radius - width / 2.0
        outer = radius + width / 2.0
        ss = 3                                  # 3x3 超采样
        for py in range(size):
            for px in range(size):
                cov = 0
                for sy in range(ss):
                    for sx in range(ss):
                        fx = px + (sx + 0.5) / ss - cx
                        fy = py + (sy + 0.5) / ss - cy
                        dist = math.hypot(fx, fy)
                        if not (inner <= dist <= outer):
                            continue
                        # 角度：12 点为 0，顺时针增大
                        ang = math.degrees(math.atan2(fx, -fy)) % 360.0
                        rel = (ang - start_deg) % 360.0
                        if rel <= span_deg:
                            cov += 1
                if cov:
                    aa = int(255 * cov / (ss * ss))
                    if aa:
                        put(px, py, rgb_col, min(255, aa if a == 255 else aa))

    # 底槽（整圈，暗色）
    blend_ring(r, thick, 0x003D332E, 255)
    # 进度弧（从 12 点顺时针）
    if percent is not None and percent > 0:
        span = min(100.0, float(percent)) * 3.6
        blend_ring(r, thick, rgb, 255, 0.0, span)

    # —— 中心数字：不用 GDI 文字（DIB 上写字拿不到 alpha，实测字会丢），
    #    改用内置 4x6 点阵自己画，小尺寸下更清晰可控。
    text = "--" if percent is None else str(int(percent))
    txt_rgb = 0x00FFFFFF if (percent is None or charging or percent < 60) else 0x00141414

    if text != "--":
        # 每个数字 4x6 点，字符间距 1 点；按可用宽度选缩放
        n = len(text)
        scale = max(1, (size - 2 * thick) // (n * 5 - 1))
        scale = max(1, min(scale, 2))
        cw, chh = 4 * scale, 6 * scale
        gap = scale
        total_w = n * cw + (n - 1) * gap
        x0 = int(round((size - total_w) / 2.0))
        y0 = int(round((size - chh) / 2.0))
        for k, ch in enumerate(text):
            pat = _DIGITS.get(ch)
            if not pat:
                continue
            ox = x0 + k * (cw + gap)
            for ry in range(6):
                for rx in range(4):
                    if pat[ry][rx] == '1':
                        for sy in range(scale):
                            for sx in range(scale):
                                put(ox + rx * scale + sx, y0 + ry * scale + sy,
                                    txt_rgb, 255)
    else:
        # 未知：中间画一条横杠
        bar_w = max(2, size // 2)
        bar_h = max(1, size // 6)
        bx = (size - bar_w) // 2
        by = (size - bar_h) // 2
        for py in range(by, by + bar_h):
            for px in range(bx, bx + bar_w):
                put(px, py, 0x008B93A3, 255)

    # —— 生成图标（mask 用 1bpp 全 0：表示按颜色位图 alpha 走）
    mask = gdi32.CreateBitmap(size, size, 1, 1, None)
    ii = ICONINFO()
    ii.fIcon = True
    ii.hbmColor = hbmp
    ii.hbmMask = mask
    icon = user32.CreateIconIndirect(byref(ii))

    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteObject(mask)
    gdi32.DeleteDC(hdc)
    user32.ReleaseDC(None, hdc_screen)
    return icon


# ---------------------------------------------------------------- 单实例
def acquire_single_instance():
    """返回互斥体句柄；已有实例则返回 None。"""
    h = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
        if h:
            kernel32.CloseHandle(h)
        return None
    return h


def signal_existing_instance():
    """把已有托盘实例的面板唤到前台（按窗口类名找）。"""
    hwnd = user32.FindWindowW(WINDOW_CLASS, None)
    if not hwnd:
        return False
    cds = COPYDATASTRUCT()
    cds.dwData = ctypes.c_void_p(1)
    cds.cbData = 0
    cds.lpData = None
    user32.SendMessageW(hwnd, WM_COPYDATA, None, byref(cds))
    return True


# ---------------------------------------------------------------- 托盘主体
class Tray:
    def __init__(self, interval=60):
        self.interval = max(30, min(1800, interval))
        self.hwnd = None
        self.icon = None
        self.nid = NOTIFYICONDATAW()
        self.last = []
        self.last_update = None
        self._busy = False                # 读取互锁：WM_TIMER 堆积时防止并发读 HID
        self._wndproc_ref = None
        self.class_name = WINDOW_CLASS
        self._stopping = False

    # -- 窗口 --
    def _register_class(self):
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc_ref = WNDPROC(self._wndproc)
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = self.class_name
        user32.RegisterClassW(byref(wc))
        return hinst

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYICON:
            # §一.3：左键立刻刷新；右键打开/前置面板
            if lparam == WM_LBUTTONUP:
                self.refresh()
            elif lparam in (WM_RBUTTONUP, WM_LBUTTONDBLCLK):
                self._open_panel()
            return 0
        if msg == WM_COPYDATA:
            # 另一个实例想开面板 → 这里把它唤到前台
            self._open_panel()
            return 1
        if msg == WM_COMMAND:
            cmd = wparam & 0xFFFF
            if cmd == IDM_REFRESH:
                self.refresh()
            elif cmd == IDM_PANEL:
                self._open_panel()
            elif cmd == IDM_EXIT:
                self._cleanup()
                user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_TIMER:
            self.refresh()
            return 0
        if msg == WM_DESTROY:
            # §四.3：不 PostQuitMessage 会留僵尸进程 + 占着单实例锁
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self):
        # §四.3（坑 3）：先 SetForegroundWindow 再 TrackPopupMenu，菜单才会正确消失
        from my_devices import autostart_lnk
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, IDM_REFRESH, "立即刷新")
        user32.AppendMenuW(menu, MF_STRING, IDM_PANEL, "打开面板")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        # §四.7：只 *读* 自启状态显示，绝不在菜单里替用户改
        enabled = os.path.exists(autostart_lnk())
        text = "开机自启：已启用" if enabled else "开机自启：未启用"
        user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, IDM_AUTOSTART, text)
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, IDM_EXIT, "退出")
        pt = wintypes.POINT()
        user32.GetCursorPos(byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_BOTTOMALIGN,
                              pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)

    def _open_panel(self):
        """§一.3：打开/前置简洁面板，已开则不重复开。

        Panel 自己持有单实例（全局 _PANEL 引用 + Tk 窗口查重）。
        """
        from my_devices import open_panel_singleton
        open_panel_singleton()

    # -- 图标/提示 --
    def _update_icon(self, rows):
        pct, chg = None, False
        if rows:
            r = rows[0]
            pct, chg = r["percent"], r["charging"]
        new_icon = make_icon(pct, chg)
        old_icon = self.icon
        self.icon = new_icon                 # 坑 2: 换图标先记旧的，删旧的
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.hIcon = self.icon
        self.nid.szTip = self._tooltip(rows)[:127]
        # §四.5：用 NIM_MODIFY 更新，不删了再加
        shell32.Shell_NotifyIconW(NIM_MODIFY, byref(self.nid))
        if old_icon:
            user32.DestroyIcon(old_icon)

    def _tooltip(self, rows):
        """§一.3：悬停显示「设备名：电量（更新时间）」。"""
        stamp = (self.last_update.strftime("%H:%M:%S")
                 if self.last_update else "尚未更新")
        if not rows:
            return f"无线外设电量：读取失败 —— 动一下鼠标（{stamp}）"
        parts = []
        for r in rows:
            p = f"{r['percent']}%" if r["percent"] is not None else r["level_text"]
            if r["charging"]:
                p += " 充电中"
            parts.append(f"{r['name']}：{p}")
        return f"{' / '.join(parts)}（{stamp}）"

    def refresh(self):
        from my_devices import read_all
        import datetime
        # 互斥：WM_TIMER 间隔短于一次读取耗时（设备休眠时读取会重试到 1s+）时，
        # 消息队列里会堆积多个 WM_TIMER，导致并发访问 HID、响应错位。
        if self._busy:
            return
        self._busy = True
        try:
            rows = read_all()
            self.last = rows
            self.last_update = datetime.datetime.now()
            self._update_icon(rows)
        finally:
            self._busy = False

    def run(self):
        set_dpi_aware()
        hinst = self._register_class()
        self.hwnd = user32.CreateWindowExW(0, self.class_name, "电量托盘", 0,
                                           0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowEx 失败 err={ctypes.get_last_error()}")
        self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.nid.hWnd = self.hwnd
        self.nid.uID = 1
        self.nid.uCallbackMessage = WM_TRAYICON
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.szTip = "无线外设电量：初始化…"
        self.icon = make_icon(None)
        self.nid.hIcon = self.icon
        if not shell32.Shell_NotifyIconW(NIM_ADD, byref(self.nid)):
            raise OSError(f"Shell_NotifyIcon 失败 err={ctypes.get_last_error()}")

        self.refresh()
        # §一.4：SetTimer 驱动，空闲时不轮询、不 sleep 循环
        user32.SetTimer(self.hwnd, 1, self.interval * 1000, None)

        msg = wintypes.MSG()
        # 坑 7: 必须 TranslateMessage，否则菜单快捷键失效
        while user32.GetMessageW(byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))
        self._cleanup()
        return 0

    def _cleanup(self):
        try:
            shell32.Shell_NotifyIconW(NIM_DELETE, byref(self.nid))
        except Exception:                                   # noqa: BLE001
            pass
        if self.icon:
            user32.DestroyIcon(self.icon)   # 坑 2
            self.icon = None


def run_tray(interval=60):
    """托盘入口：保证单实例；第二次启动则把已有实例的面板唤到前台。"""
    mutex = acquire_single_instance()
    if mutex is None:
        if signal_existing_instance():
            print("托盘已在运行 —— 已唤出它的面板。")
        else:
            from my_devices import open_panel_singleton
            print("托盘已在运行。")
            open_panel_singleton()
        return 0
    try:
        return Tray(interval).run()
    finally:
        kernel32.CloseHandle(mutex) if mutex else None