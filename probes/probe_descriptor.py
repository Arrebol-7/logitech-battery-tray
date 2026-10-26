"""读取 HID 报告描述符原文（用 HidD_GetReportDescriptor 或 IOCTL）。

报告描述符是设备的自白书：直接告诉我们它声明了哪些 Report ID、
哪些 usage，不靠猜。
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctypes import wintypes

import probe_channels as pc

kernel32 = pc.kernel32
hid = pc.hid
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
OPEN_EXISTING = 3

# HidD_GetReportDescriptor 并非所有系统都导出，用 IOCTL 更可靠
IOCTL_HID_GET_REPORT_DESCRIPTOR = 0x000B01C2
IOCTL_HID_GET_INDEXED_STRING = 0x000B01DE
IOCTL_HID_GET_STRING = 0x000B01E4

class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]

kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

def get_descriptor(path):
    h = kernel32.CreateFileW(path, 0, 3, None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE:
        h = kernel32.CreateFileW(path, GENERIC_READ, 3, None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE:
        return None, -1
    buf = ctypes.create_string_buffer(4096)
    n = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(h, IOCTL_HID_GET_REPORT_DESCRIPTOR,
                                  None, 0, buf, 4096, ctypes.byref(n), None)
    kernel32.CloseHandle(h)
    if not ok:
        return None, ctypes.get_last_error()
    return buf.raw[:n.value], 0

def parse_items(data):
    """极简 HID 描述符解析：提取 Report ID / Usage Page / Usage / 长度。"""
    out = []
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0xFE:  # long item
            if i + 2 < len(data):
                sz = data[i + 1]
                i += 3 + sz
            else:
                break
            continue
        size = b & 0x03
        if size == 3:
            size = 4
        typ = (b >> 2) & 0x03
        tag = (b >> 4) & 0x0F
        val = int.from_bytes(data[i + 1:i + 1 + size], "little") if size else 0
        # typ: 0=main 1=global 2=local
        names = {
            (1, 0): "UsagePage", (1, 1): "LogicalMin", (1, 2): "LogicalMax",
            (1, 7): "ReportSize", (1, 8): "ReportID", (1, 9): "ReportCount",
            (2, 0): "Usage",
            (0, 8): "Input", (0, 9): "Output", (0, 11): "Feature",
            (0, 10): "Collection", (0, 12): "EndCollection",
        }
        nm = names.get((typ, tag))
        if nm:
            out.append((i, nm, val, data[i:i + 1 + size].hex(" ")))
        i += 1 + size
    return out

def main():
    paths = pc.list_vendor_paths()
    print(f"vendor 接口 {len(paths)} 个\n")
    for path in paths:
        tag = path.split("#")[1]
        print("=" * 74)
        print(f"接口 {tag}")
        print("=" * 74)
        data, err = get_descriptor(path)
        if data is None:
            print(f"  无法读取描述符 err={err}\n")
            continue
        print(f"  描述符长度 {len(data)} 字节")
        print(f"  原文: {data.hex(' ')}\n")
        items = parse_items(data)
        caps = {}
        cur = {}
        print("  解析:")
        for off, nm, val, raw in items:
            if nm == "ReportID":
                cur["rid"] = val
            if nm == "UsagePage":
                cur["up"] = val
            if nm == "Usage":
                if "up" in cur:
                    cur["usage"] = val
            if nm == "ReportSize":
                cur["rsize"] = val
            if nm == "ReportCount":
                cur["rcount"] = val
            if nm in ("Input", "Output", "Feature"):
                rid = cur.get("rid", 0)
                bits = cur.get("rsize", 0) * cur.get("rcount", 0)
                key = (nm, rid)
                caps[key] = caps.get(key, 0) + bits
                extra = ""
                if val & 0x02:
                    extra = " [DATA,VAR]"
                elif val & 0x01:
                    extra = " [CONST]"
                print(f"    [0x{off:03X}] {nm} rid=0x{rid:02X} "
                      f"usagePage=0x{cur.get('up', 0):04X} "
                      f"usage=0x{cur.get('usage', 0):04X} "
                      f"size={bits} bits{extra}")
            elif nm in ("UsagePage", "ReportID"):
                print(f"    [0x{off:03X}] {nm} = 0x{val:04X}")
        print()
        print("  汇总 (报告总字节数, 不含 Report ID):")
        for (kind, rid), bits in sorted(caps.items()):
            print(f"    {kind:8s} rid=0x{rid:02X}  {(bits + 7) // 8} 字节")
        print()
    return 0

if __name__ == "__main__":
    sys.exit(main())