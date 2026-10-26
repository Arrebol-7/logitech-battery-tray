"""暴力探测：哪个 Report ID / 哪个接口能收到 HID++ 响应。

思路：对每个 (接口, Report ID) 组合发一条最小请求，看有没有任何字节回来。
能回应的组合就是正确通道。这是确定性的，不用猜。
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctypes import wintypes

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GUID_DEVINTERFACE_HID = "{4D1E55B2-F16F-11CF-88CB-001111000030}"
DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x02, 0x10
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 0x01, 0x02
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_IO_PENDING, WAIT_OBJECT_0, WAIT_TIMEOUT = 997, 0, 258

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

class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]

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
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                                  wintypes.LPCWSTR]
kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED),
                                         ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

TARGET_VID, TARGET_PID = 0x046D, 0xC547

def list_vendor_paths():
    guid = GUID(GUID_DEVINTERFACE_HID)
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    out = []
    if hdev == INVALID_HANDLE_VALUE:
        return out
    try:
        idx = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    hdev, None, ctypes.byref(guid), idx, ctypes.byref(did)):
                break
            idx += 1
            needed = wintypes.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), None, 0, ctypes.byref(needed), None)
            if not needed.value:
                continue
            buf = ctypes.create_string_buffer(needed.value)
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(did), buf, needed.value,
                    ctypes.byref(needed), None):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            h = kernel32.CreateFileW(path, 0, 3, None, OPEN_EXISTING, 0, None)
            if h == INVALID_HANDLE_VALUE:
                continue
            a = HIDD_ATTRIBUTES()
            a.Size = ctypes.sizeof(a)
            ok = hid.HidD_GetAttributes(h, ctypes.byref(a))
            kernel32.CloseHandle(h)
            if ok and a.VendorID == TARGET_VID and a.ProductID == TARGET_PID \
               and "mi_02" in path.lower():
                out.append(path)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out

class Dev:
    def __init__(self, path):
        self.h = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                                      3, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        self.ev = kernel32.CreateEventW(None, True, False, None)

    def write(self, data, t=500):
        ov = OVERLAPPED(); ov.hEvent = self.ev
        kernel32.ResetEvent(self.ev)
        w = wintypes.DWORD()
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        if not kernel32.WriteFile(self.h, buf, len(data), ctypes.byref(w), ctypes.byref(ov)):
            if ctypes.get_last_error() != ERROR_IO_PENDING:
                return -1
            if kernel32.WaitForSingleObject(self.ev, t) != WAIT_OBJECT_0:
                kernel32.CancelIoEx(self.h, ctypes.byref(ov)); return -2
            if not kernel32.GetOverlappedResult(self.h, ctypes.byref(ov), ctypes.byref(w), False):
                return -3
        return w.value

    def read(self, size, t=500):
        ov = OVERLAPPED(); ov.hEvent = self.ev
        kernel32.ResetEvent(self.ev)
        g = wintypes.DWORD()
        buf = ctypes.create_string_buffer(size)
        if not kernel32.ReadFile(self.h, buf, size, ctypes.byref(g), ctypes.byref(ov)):
            if ctypes.get_last_error() != ERROR_IO_PENDING:
                return None
            if kernel32.WaitForSingleObject(self.ev, t) != WAIT_OBJECT_0:
                kernel32.CancelIoEx(self.h, ctypes.byref(ov)); return None
            if not kernel32.GetOverlappedResult(self.h, ctypes.byref(ov), ctypes.byref(g), False):
                return None
        return buf.raw[:g.value]

    def drain(self, t=120):
        n = 0
        while self.read(32, t) is not None:
            n += 1
            if n > 5:
                break

    def close(self):
        if self.ev: kernel32.CloseHandle(self.ev); self.ev = None
        if self.h: kernel32.CloseHandle(self.h); self.h = None

def main():
    paths = list_vendor_paths()
    print(f"vendor 接口数: {len(paths)}")
    for p in paths:
        print("  ", p)

    # Report ID 候选：HID++ 2.0 常用 0x10/0x11；1.0 接收器用 0x20/0x21
    report_ids = [0x10, 0x11, 0x20, 0x21]
    dev_indexes = [0xFF, 0x01, 0x00]
    # root feature(0x0000).getFeature(0x1000 BATTERY_VOLTAGE)
    payload = bytes([0x00, 0x00, 0x10, 0x00])

    for path in paths:
        print("\n" + "=" * 70)
        print("接口:", path.split("#")[1][:24])
        try:
            d = Dev(path)
        except Exception as e:
            print("  打开失败", e); continue
        if d.h == INVALID_HANDLE_VALUE:
            print("  CreateFile 失败 err=", ctypes.get_last_error()); continue

        for rid in report_ids:
            for di in dev_indexes:
                d.drain()
                short = rid in (0x10, 0x20)
                size = 7 if short else 20
                msg = bytes([rid, di]) + payload
                msg = msg + bytes(size - len(msg))
                wr = d.write(msg[:size])
                resp = d.read(size, 600)
                if resp:
                    print(f"  rid=0x{rid:02X} dev=0x{di:02X} -> 收到 {len(resp)}B: {resp.hex(' ')}")
                elif wr < 0:
                    print(f"  rid=0x{rid:02X} dev=0x{di:02X} -> 写失败({wr})")
        d.close()

    # 另外：不写任何东西，纯监听 Input Report（看设备是否主动上报电量）
    print("\n" + "=" * 70)
    print("纯监听 Input Report（3 秒，不发送任何东西）:")
    for path in paths:
        d = Dev(path)
        if d.h == INVALID_HANDLE_VALUE:
            continue
        d.drain()
        got = 0
        for _ in range(30):
            r = d.read(32, 100)
            if r:
                print(f"  [{path.split('#')[1][:24]}] {r.hex(' ')}")
                got += 1
                if got > 8:
                    break
        if not got:
            print(f"  [{path.split('#')[1][:24]}] 静默")
        d.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())