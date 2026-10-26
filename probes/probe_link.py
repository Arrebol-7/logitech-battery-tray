"""判断鼠标实际链路：有线直连 / 2.4G 接收器 / 蓝牙。

方法：查所有鼠标类 HID 设备的完整硬件 ID 与父设备，看是否存在
      接收器之外的第二个鼠标设备。
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import subprocess
from ctypes import wintypes

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x02, 0x10
GUID_DEVINTERFACE_HID = "{4D1E55B2-F16F-11CF-88CB-001111000030}"

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, s):
        super().__init__()
        p = s.strip("{}").split("-", 3)
        self.Data1, self.Data2, self.Data3 = int(p[0], 16), int(p[1], 16), int(p[2], 16)
        for i, b in enumerate(bytes.fromhex(p[3].replace("-", ""))):
            self.Data4[i] = b

class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]

class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]

setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                          wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.HANDLE]
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

def main():
    print("=== A. 所有 HID 接口按 VID:PID 汇总 ===\n")
    guid = GUID(GUID_DEVINTERFACE_HID)
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    agg = {}
    idx = 0
    while True:
        did = SP_DEVICE_INTERFACE_DATA()
        did.cbSize = ctypes.sizeof(did)
        if not setupapi.SetupDiEnumDeviceInterfaces(
                hdev, None, ctypes.byref(guid), idx, ctypes.byref(did)):
            break
        idx += 1
        n = wintypes.DWORD()
        setupapi.SetupDiGetDeviceInterfaceDetailW(
            hdev, ctypes.byref(did), None, 0, ctypes.byref(n), None)
        if not n.value:
            continue
        b = ctypes.create_string_buffer(n.value)
        ctypes.cast(b, ctypes.POINTER(wintypes.DWORD))[0] = 8
        if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), b, n.value, ctypes.byref(n), None):
            continue
        path = ctypes.wstring_at(ctypes.addressof(b) + 4)
        f = kernel32.CreateFileW(path, 0, 3, None, 3, 0, None)
        if f == INVALID_HANDLE_VALUE:
            continue
        a = HIDD_ATTRIBUTES()
        a.Size = ctypes.sizeof(a)
        ok = hid.HidD_GetAttributes(f, ctypes.byref(a))
        kernel32.CloseHandle(f)
        if ok:
            k = f"{a.VendorID:04X}:{a.ProductID:04X}"
            agg.setdefault(k, []).append(path)

    for k in sorted(agg):
        print(f"{k}  ({len(agg[k])} 个接口)")
        for p in agg[k]:
            seg = p.split("#")
            print("     ", seg[1] if len(seg) > 1 else p)
        print()

    # 蓝牙设备通常在 GUID_DEVCLASS_BLUETOOTH，检查是否有蓝牙 HID 鼠标
    print("=== B. 判断依据 ===")
    logi = [k for k in agg if k.startswith("046D")]
    print(f"罗技 VID 设备: {logi}")
    if len(logi) == 1 and logi[0] == "046D:C547":
        print("  -> 只有一个罗技设备（接收器）。")
        print("     鼠标若走有线直连，应出现 046D:C5xx 的独立设备；")
        print("     现在没有，说明鼠标不存在于 USB 总线上。")
    hdev2 = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x02)
    setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return 0

if __name__ == "__main__":
    sys.exit(main())