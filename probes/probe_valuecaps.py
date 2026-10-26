"""用 HidP_GetValueCaps 读 vendor 接口声明的全部能力。

HidP_GetValueCaps 是标准 API，比 IOCTL 描述符更可靠，
直接列出：每个 Report ID 下有哪些 usage、多少位、逻辑范围。
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
OPEN_EXISTING = 3

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

class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", wintypes.USHORT),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", wintypes.BOOL),
        ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT),
        ("LinkUsage", wintypes.USHORT),
        ("LinkUsagePage", wintypes.USHORT),
        ("IsRange", wintypes.BOOL),
        ("IsStringRange", wintypes.BOOL),
        ("IsDesignatorRange", wintypes.BOOL),
        ("IsAbsolute", wintypes.BOOL),
        ("HasNull", wintypes.BOOL),
        ("Reserved", ctypes.c_ubyte),
        ("BitSize", wintypes.USHORT),
        ("ReportCount", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT * 5),
        ("UnitsExp", wintypes.ULONG),
        ("Units", wintypes.ULONG),
        ("LogicalMin", ctypes.c_long),
        ("LogicalMax", ctypes.c_long),
        ("PhysicalMin", ctypes.c_long),
        ("PhysicalMax", ctypes.c_long),
        ("Union1", wintypes.USHORT * 2),
        ("Union2", wintypes.USHORT * 2),
        ("Union3", wintypes.USHORT * 2),
    ]

hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetValueCaps.argtypes = [ctypes.c_int, ctypes.POINTER(HIDP_VALUE_CAPS),
                                  ctypes.POINTER(wintypes.USHORT), ctypes.c_void_p]

HidP_Input, HidP_Output, HidP_Feature = 0, 1, 2

def dump(path):
    print("=" * 76)
    print("接口:", path.split("#")[1])
    print("=" * 76)
    h = kernel32.CreateFileW(path, 0, 3, None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE:
        print("  打开失败"); return
    pp = ctypes.c_void_p()
    if not hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
        print("  GetPreparsedData 失败"); kernel32.CloseHandle(h); return
    caps = HIDP_CAPS()
    if not hid.HidP_GetCaps(pp, ctypes.byref(caps)):
        print("  GetCaps 失败"); hid.HidD_FreePreparsedData(pp)
        kernel32.CloseHandle(h); return

    print(f"  UsagePage/Usage = {caps.UsagePage:04X}/{caps.Usage:04X}")
    print(f"  In/Out/Feat = {caps.InputReportByteLength}/"
          f"{caps.OutputReportByteLength}/{caps.FeatureReportByteLength}")
    print(f"  ValueCaps 数量: in={caps.NumberInputValueCaps} "
          f"out={caps.NumberOutputValueCaps} feat={caps.NumberFeatureValueCaps}")
    print(f"  ButtonCaps 数量: in={caps.NumberInputButtonCaps} "
          f"out={caps.NumberOutputButtonCaps} feat={caps.NumberFeatureButtonCaps}")

    for kind, cnt, name in ((HidP_Input, caps.NumberInputValueCaps, "Input"),
                            (HidP_Output, caps.NumberOutputValueCaps, "Output"),
                            (HidP_Feature, caps.NumberFeatureValueCaps, "Feature")):
        if not cnt:
            continue
        print(f"\n  --- {name} ValueCaps ({cnt}) ---")
        arr = (HIDP_VALUE_CAPS * cnt)()
        length = wintypes.USHORT(cnt)
        if hid.HidP_GetValueCaps(kind, arr, ctypes.byref(length), pp):
            for i in range(length.value):
                c = arr[i]
                lo = c.Union2[0] if hasattr(c, "Union2") else 0
                if c.IsRange:
                    usage = f"0x{c.Union1[0]:04X}..0x{c.Union1[1]:04X}"
                else:
                    usage = f"0x{c.Union1[0]:04X}"
                print(f"    [{i}] rid=0x{c.ReportID:02X} usagePage=0x{c.UsagePage:04X} "
                      f"usage={usage} bitSize={c.BitSize} count={c.ReportCount} "
                      f"logMin={c.LogicalMin} logMax={c.LogicalMax} "
                      f"abs={bool(c.IsAbsolute)} bitfield={bool(c.BitField)}")
        else:
            print("    GetValueCaps 失败")

    hid.HidD_FreePreparsedData(pp)
    kernel32.CloseHandle(h)
    print()

def main():
    for p in pc.list_vendor_paths():
        dump(p)
    return 0

if __name__ == "__main__":
    sys.exit(main())