"""读 HID 报告描述符 + 验证写通道。

关键：确认 mi_02&col01 期望的 Output Report 到底多长、
      以及 Report ID 是 0x10 还是别的。
用 HidD_GetPreparsedData/HidP_GetCaps 拿精确长度，
再用 HidD_GetFeature 试读 Feature Report。
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctypes import wintypes

from probe_channels import list_vendor_paths
import probe_channels as pc

kernel32 = pc.kernel32
hid = pc.hid
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]

hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_SetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_GetIndexedString.argtypes = [wintypes.HANDLE, wintypes.ULONG,
                                      ctypes.c_void_p, wintypes.ULONG]

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
OPEN_EXISTING = 3

def main():
    paths = list_vendor_paths()
    for path in paths:
        tag = path.split("#")[1]
        print("=" * 74)
        print(f"接口: {tag}")
        print("=" * 74)

        h = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, 3, None,
                                 OPEN_EXISTING, 0, None)
        if h == INVALID_HANDLE_VALUE:
            print("  读写打开失败，改只读")
            h = kernel32.CreateFileW(path, 0, 3, None, OPEN_EXISTING, 0, None)
        if h == INVALID_HANDLE_VALUE:
            print("  打开失败，跳过"); continue

        pp = ctypes.c_void_p()
        if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
            caps = HIDP_CAPS()
            if hid.HidP_GetCaps(pp, ctypes.byref(caps)):
                print(f"  UsagePage/Usage : {caps.UsagePage:04X}/{caps.Usage:04X}")
                print(f"  Input  Report   : {caps.InputReportByteLength} 字节")
                print(f"  Output Report   : {caps.OutputReportByteLength} 字节")
                print(f"  Feature Report  : {caps.FeatureReportByteLength} 字节")
            hid.HidD_FreePreparsedData(pp)

        # 试读 Feature Report（如果支持）
        caps2 = HIDP_CAPS()
        pp2 = ctypes.c_void_p()
        if hid.HidD_GetPreparsedData(h, ctypes.byref(pp2)):
            hid.HidP_GetCaps(pp2, ctypes.byref(caps2))
            fl = caps2.FeatureReportByteLength
            hid.HidD_FreePreparsedData(pp2)
            if fl:
                buf = ctypes.create_string_buffer(fl)
                buf[0] = 0x00
                ok = hid.HidD_GetFeature(h, buf, fl)
                print(f"  GetFeature(rid=0) -> {'OK ' + buf.raw.hex(' ') if ok else '失败 err=%d' % ctypes.get_last_error()}")

        # 用 HidD_SetFeature 发一条 HID++ 请求（另一条通路，绕过 WriteFile）
        print("  --- 用 HidD_SetFeature 发 HID++ (rid=0x10) ---")
        for fl_size in (7, 8):
            buf = ctypes.create_string_buffer(fl_size)
            buf[0] = 0x10
            buf[1] = 0xFF
            buf[2] = 0x00
            buf[3] = 0x01
            ok = hid.HidD_SetFeature(h, buf, fl_size)
            print(f"    SetFeature size={fl_size}: {'OK' if ok else '失败 err=%d' % ctypes.get_last_error()}")

        kernel32.CloseHandle(h)
        print()

    # 另外：查 Windows 是否暴露了电量属性（设备属性 GUID）
    print("=" * 74)
    print("Windows 电量属性探测")
    print("=" * 74)
    print("  用 PowerShell 查 PnP 设备的 Battery 属性")
    return 0

if __name__ == "__main__":
    sys.exit(main())