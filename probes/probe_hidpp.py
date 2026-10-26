"""HID++ 2.0 探测：走 mi_02 vendor 接口，读 GPW2 电量特性。

关键验证点：
  1. vendor 接口能否用 GENERIC_READ|GENERIC_WRITE 打开（系统是否独占）
  2. 0x1000 BATTERY_VOLTAGE  /  0x1004 UNIFIED_BATTERY  特性是否存在
  3. 实际读回的字节

坑（PROMPT.md 第 54 条）：绝不在同一句柄上"阻塞 ReadFile + 并发 WriteFile"。
这里全部用 OVERLAPPED + 事件超时，读写分开。
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
DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x01
FILE_SHARE_WRITE = 0x02
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, s):
        super().__init__()
        p = s.strip("{}").split("-", 3)
        self.Data1 = int(p[0], 16)
        self.Data2 = int(p[1], 16)
        self.Data3 = int(p[2], 16)
        for i, b in enumerate(bytes.fromhex(p[3].replace("-", ""))):
            self.Data4[i] = b

class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]

class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID),
                ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]

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
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID),
    wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
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

ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258

TARGET_VID, TARGET_PID = 0x046D, 0xC547

# HID++ 2.0
REPORT_SHORT = 0x10
REPORT_LONG = 0x11
DEV_INDEX = 0xFF  # 接收器自身；0x01..0x06 是配对的设备

FEAT_BATTERY_VOLTAGE = 0x1000
FEAT_UNIFIED_BATTERY = 0x1004
FEAT_BATTERY_STATUS = 0x1001

def find_vendor_interfaces():
    """返回 (path, access_ok) —— access_ok 表示能否读写打开。"""
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

            h = kernel32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                     None, OPEN_EXISTING, 0, None)
            if h == INVALID_HANDLE_VALUE:
                continue
            attrs = HIDD_ATTRIBUTES()
            attrs.Size = ctypes.sizeof(attrs)
            ok = hid.HidD_GetAttributes(h, ctypes.byref(attrs))
            kernel32.CloseHandle(h)
            if not ok or attrs.VendorID != TARGET_VID or attrs.ProductID != TARGET_PID:
                continue
            # vendor 通道 = mi_02
            if "mi_02" not in path.lower():
                continue
            rw = kernel32.CreateFileW(
                path, GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
            access_ok = rw != INVALID_HANDLE_VALUE
            if access_ok:
                kernel32.CloseHandle(rw)
            out.append((path, access_ok))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out

class Hiddev:
    """独立读写句柄：OVERLAPPED + 事件超时，绝不阻塞读+并发写。"""

    def __init__(self, path, write=True):
        access = GENERIC_READ | (GENERIC_WRITE if write else 0)
        self.h = kernel32.CreateFileW(
            path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
            OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if self.h == INVALID_HANDLE_VALUE:
            raise OSError(f"CreateFile failed, err={ctypes.get_last_error()}")
        self.ev = kernel32.CreateEventW(None, True, False, None)
        self.ov = OVERLAPPED()

    def write(self, data, timeout_ms=1000):
        self.ov = OVERLAPPED()
        self.ov.hEvent = self.ev
        kernel32.ResetEvent(self.ev)
        written = wintypes.DWORD()
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        ok = kernel32.WriteFile(self.h, buf, len(data), ctypes.byref(written),
                                ctypes.byref(self.ov))
        if not ok:
            err = ctypes.get_last_error()
            if err != ERROR_IO_PENDING:
                raise OSError(f"WriteFile err={err}")
            if kernel32.WaitForSingleObject(self.ev, timeout_ms) != WAIT_OBJECT_0:
                kernel32.CancelIoEx(self.h, ctypes.byref(self.ov))
                raise TimeoutError("write timeout")
            if not kernel32.GetOverlappedResult(self.h, ctypes.byref(self.ov),
                                                ctypes.byref(written), False):
                raise OSError(f"write overlapped err={ctypes.get_last_error()}")
        return written.value

    def read(self, size, timeout_ms=1000):
        self.ov = OVERLAPPED()
        self.ov.hEvent = self.ev
        kernel32.ResetEvent(self.ev)
        got = wintypes.DWORD()
        buf = ctypes.create_string_buffer(size)
        ok = kernel32.ReadFile(self.h, buf, size, ctypes.byref(got),
                               ctypes.byref(self.ov))
        if not ok:
            err = ctypes.get_last_error()
            if err != ERROR_IO_PENDING:
                raise OSError(f"ReadFile err={err}")
            if kernel32.WaitForSingleObject(self.ev, timeout_ms) != WAIT_OBJECT_0:
                kernel32.CancelIoEx(self.h, ctypes.byref(self.ov))
                return None
            if not kernel32.GetOverlappedResult(self.h, ctypes.byref(self.ov),
                                                ctypes.byref(got), False):
                raise OSError(f"read overlapped err={ctypes.get_last_error()}")
        return buf.raw[:got.value]

    def close(self):
        if self.ev:
            kernel32.CloseHandle(self.ev)
            self.ev = None
        if self.h and self.h != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self.h)
            self.h = None

def hidpp20_call(dev, dev_index, feature_index, function, params=b"", long=False):
    """发一条 HID++ 2.0 请求并读响应。"""
    if long:
        msg = bytes([REPORT_LONG, dev_index, feature_index, function]) + params
        msg = msg + bytes(16 - len(msg))
        rid = REPORT_LONG
    else:
        msg = bytes([REPORT_SHORT, dev_index, feature_index, function]) + params
        msg = msg + bytes(7 - len(msg))
        rid = REPORT_SHORT

    dev.write(msg)
    # 读响应：可能夹杂其它设备的通知，过滤匹配的 dev_index
    for _ in range(8):
        resp = dev.read(20 if long else 7, timeout_ms=1500)
        if resp is None:
            return None, "timeout"
        if len(resp) >= 3 and resp[0] in (REPORT_SHORT, REPORT_LONG):
            if resp[1] == dev_index or resp[1] == 0xFF:
                if resp[2] == feature_index and resp[3] == function:
                    return resp, None
                # 有 error 位
                if resp[3] == 0x8F or resp[1] == 0xFF and resp[2] == 0xFF:
                    return resp, f"hidpp error code=0x{resp[5]:02X}"
    return None, "no matching response"

def get_feature_index(dev, dev_index, feat_id):
    """0x0000 root 的 getFeature(feat_id) -> index"""
    resp, err = hidpp20_call(dev, dev_index, 0x00, 0x00, bytes([feat_id >> 8, feat_id & 0xFF]))
    if resp is None:
        return None, err
    if resp[3] == 0x8F:
        return None, f"error 0x{resp[5]:02X}"
    return resp[1], None

def main():
    print("=== 1. 找 vendor 接口 (mi_02) ===")
    ifs = find_vendor_interfaces()
    if not ifs:
        print("没找到 046D:C547 的 mi_02 接口")
        return 1
    for path, ok in ifs:
        print(f"  {'[可读写]' if ok else '[只读!]'} {path}")
        if not ok:
            print("     ^ 无法读写打开 —— HID++ 发不出去，需改走 Input Report 监听")
    path, access_ok = ifs[0]
    if not access_ok:
        return 2

    print("\n=== 2. 打开读写句柄 ===")
    try:
        dev = Hiddev(path, write=True)
    except OSError as e:
        print("  打开失败:", e)
        return 3
    print("  OK")

    try:
        for dev_index in (0xFF, 0x01):
            print(f"\n=== 3. dev_index=0x{dev_index:02X} 探测特性 ===")
            for feat_id, name in ((FEAT_BATTERY_VOLTAGE, "BATTERY_VOLTAGE 0x1000"),
                                  (FEAT_UNIFIED_BATTERY, "UNIFIED_BATTERY 0x1004"),
                                  (FEAT_BATTERY_STATUS, "BATTERY_STATUS 0x1001")):
                idx, err = get_feature_index(dev, dev_index, feat_id)
                if idx is None:
                    print(f"  {name}: 不存在/失败 ({err})")
                    continue
                if idx == 0:
                    print(f"  {name}: 特性不支持 (index=0)")
                    continue
                print(f"  {name}: feature_index=0x{idx:02X}")

                # 读具体数值
                if feat_id == FEAT_BATTERY_VOLTAGE:
                    r, e = hidpp20_call(dev, dev_index, idx, 0x00)
                    if r:
                        print(f"    getVoltage: {r.hex(' ')}")
                elif feat_id == FEAT_UNIFIED_BATTERY:
                    r, e = hidpp20_call(dev, dev_index, idx, 0x10)
                    if r:
                        print(f"    getCapabilities: {r.hex(' ')}")
                        caps_bits = int.from_bytes(r[4:6], "big")
                        if caps_bits & 0x02:
                            r2, _ = hidpp20_call(dev, dev_index, idx, 0x11)
                            if r2:
                                print(f"    getStatus(0x11): {r2.hex(' ')}")
                        else:
                            r2, _ = hidpp20_call(dev, dev_index, idx, 0x00)
                            if r2:
                                print(f"    getStatus(0x00): {r2.hex(' ')}")
                elif feat_id == FEAT_BATTERY_STATUS:
                    r, e = hidpp20_call(dev, dev_index, idx, 0x00)
                    if r:
                        print(f"    getBatteryLevelStatus: {r.hex(' ')}")
    finally:
        dev.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())