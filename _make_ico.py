"""生成应用图标 app.ico —— 圆环 + 百分比风格，与托盘图标一致。

纯标准库：GDI 画 256x256 32位 BGRA DIB -> 用像素数据手工拼 ICO 容器
（PNG 压缩内嵌，Vista+ 支持）。为什么不用 GDI 直接写 .ico：
CTypedPersistent 的 HICON->ico 需要 IconPro 样例代码，手拼容器更短。
"""
import ctypes
import ctypes.wintypes as wt
import struct
import zlib

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32

# 原型（64 位下必须声明，否则句柄截断）
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p),
                                   wt.HANDLE, wt.DWORD]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.DeleteObject.argtypes = [wt.HANDLE]
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SetPixel.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, wt.COLORREF]

user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]

SRCCOPY = 0x00CC0020


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wt.DWORD * 3)]


def hex2bgr(s):
    r = int(s[1:3], 16); g = int(s[3:5], 16); b = int(s[5:7], 16)
    return (b << 16) | (g << 8) | r


BG_CARD = hex2bgr("#1F2229")
TRACK = hex2bgr("#2E333D")
FG = hex2bgr("#F2F4F8")
FG_DIM = hex2bgr("#9BA3B4")
GREEN = hex2bgr("#3DDC84")

SIZE = 256
THICK = 26          # 圆环粗细（256px 下）
PAD = THICK // 2 + 2


def render_256():
    """用 GDI 画 256x256 BGRA，返回 (pixels bytes top-down, stride)。"""
    hdc_screen = user32.GetDC(None)
    hdc = gdi32.CreateCompatibleDC(hdc_screen)
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = SIZE
    bi.bmiHeader.biHeight = -SIZE          # top-down
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0         # BI_RGB
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc, ctypes.byref(bi), 0,
                                  ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(hdc, hbmp)

    # 数据地址
    base = bits.value

    # 1) 卡片底色铺满
    for off in range(0, SIZE * SIZE * 4, 4):
        ctypes.c_uint32.from_address(base + off).value = 0xFF000000 | BG_CARD

    # 2) 圆环底槽 + 进度弧（50% 顺时针，12 点起）—— 像素手工画
    cx = cy = SIZE / 2
    r_out = SIZE / 2 - PAD
    r_in = r_out - THICK
    r_mid = (r_out + r_in) / 2
    r_band = THICK / 2
    import math
    for y in range(SIZE):
        for x in range(SIZE):
            dx, dy = x - cx, y - cy
            d = math.hypot(dx, dy)
            if r_in <= d <= r_out:
                # 角度：12 点 = -90°，顺时针为正（屏幕 y 向下）
                ang = math.degrees(math.atan2(dx, -dy)) % 360
                color = GREEN if ang <= 180 else TRACK   # 50%
                # 抗锯齿边缘：简单阈值即可，ico 缩小后看不出
                ctypes.c_uint32.from_address(
                    base + (y * SIZE + x) * 4).value = 0xFF000000 | color
            elif abs(d - r_mid) < 1e9:
                pass  # 环外/环内保持卡片底

    # 3) 中心数字 "50" —— 复用托盘的 4x6 点阵（放大 6 倍 -> 24x36）
    D5 = ("1111", "1000", "1110", "0001", "0001", "1110")
    D0 = ("1111", "1001", "1001", "1001", "1001", "1111")
    grid = [a + "0" + b for a, b in zip(D5, D0)]   # 9 宽（含 1 列空隙）
    scale = 8                                       # 9*8=72 宽, 6*8=48 高
    gw, gh = len(grid[0]) * scale, 6 * scale
    ox, oy = int(cx - gw / 2), int(cy - gh / 2)
    for gy in range(6):
        for gx in range(len(grid[0])):
            if grid[gy][gx] == "1":
                for sy in range(scale):
                    for sx in range(scale):
                        px, py = ox + gx * scale + sx, oy + gy * scale + sy
                        off = (py * SIZE + px) * 4
                        ctypes.c_uint32.from_address(
                            base + off).value = 0xFF000000 | FG

    # 读回
    buf = (ctypes.c_ubyte * (SIZE * SIZE * 4)).from_address(base)
    data = bytes(buf)
    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc)
    user32.ReleaseDC(None, hdc_screen)
    return data


def png_from_bgra(data, w, h):
    """BGRA top-down -> PNG (RGBA)。"""
    rows = []
    for y in range(h):
        row = bytearray()
        row.append(0)                      # filter none
        for x in range(w):
            i = (y * w + x) * 4
            b, g, r, a = data[i], data[i+1], data[i+2], data[i+3]
            row += bytes((r, g, b, a))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(typ, payload):
        c = struct.pack(">I", len(payload)) + typ + payload
        return c + struct.pack(">I", zlib.crc32(typ + payload) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def build_ico(png256, sizes=((16, 1), (24, 1), (32, 1), (48, 1),
                             (64, 1), (128, 1), (256, 1))):
    """ICO 容器：256 用 PNG，其余同样内嵌 PNG（Vista+ 缩放）。"""
    n = len(sizes)
    out = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    entries = b""
    blobs = b""
    for s, _ in sizes:
        blob = png256 if s == 256 else png256   # 全部给 256，系统自己缩
        w = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32,
                               len(blob), offset)
        blobs += blob
        offset += len(blob)
    return out + entries + blobs


if __name__ == "__main__":
    data = render_256()
    png = png_from_bgra(data, SIZE, SIZE)
    ico = build_ico(png)
    with open("app.ico", "wb") as f:
        f.write(ico)
    print(f"app.ico written: {len(ico)} bytes (png {len(png)})")
