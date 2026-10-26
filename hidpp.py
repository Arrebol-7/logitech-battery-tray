"""GPW2 电量读取核心 —— 纯标准库 + ctypes，无第三方依赖。

协议结论见 PROBE-FINDINGS.md（已实测验证）：
    col01 (7B, Report ID 0x10) = 接收器自身 HID++ 1.0 寄存器
    col02 (20B, Report ID 0x11) = 转发给配对设备，用于 HID++ 2.0 特性调用

读电量流程:
    1. 枚举 046D:C547 的 mi_02 接口，区分 col01 / col02
    2. col02: root.getFeature(0x1004) -> UNIFIED_BATTERY 的 feature_index
    3. col02: feature_index.fn0 getBatteryLevelStatus -> 电量字节

本模块只做"读"，不写任何文件、不打印（调用方决定）。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

# ---------------------------------------------------------------- Win32
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GUID_DEVINTERFACE_HID = "{4D1E55B2-F16F-11CF-88CB-001111000030}"
DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x02, 0x10
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_IO_PENDING, WAIT_OBJECT_0 = 997, 0


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


# ---------------------------------------------------------------- 接口枚举
def list_interfaces(vid=0x046D, pid=0xC547):
    """返回该 VID:PID 下所有 HID 接口路径，按 (mi_XX, colNN) 排序。"""
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
            if ok and a.VendorID == vid and a.ProductID == pid:
                out.append(path)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


def find_channels(vid=0x046D, pid=0xC547):
    """定位 (col01 短报文句柄路径, col02 长报文句柄路径)。"""
    short_path = long_path = None
    for p in list_interfaces(vid, pid):
        low = p.lower()
        if "mi_02" not in low:
            continue
        if "col01" in low and short_path is None:
            short_path = p
        elif "col02" in low and long_path is None:
            long_path = p
    return short_path, long_path


# ---------------------------------------------------------------- 设备句柄
class HidDevice:
    """overlapped 读写句柄。PROMPT.md 坑 1：绝不阻塞读 + 并发写。"""

    def __init__(self, path):
        self.path = path
        self.h = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                                      3, None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        self.ev = kernel32.CreateEventW(None, True, False, None)
        self.ok = self.h != INVALID_HANDLE_VALUE

    def write(self, data, timeout_ms=500):
        if not self.ok:
            return -1
        ov = OVERLAPPED(); ov.hEvent = self.ev
        kernel32.ResetEvent(self.ev)
        w = wintypes.DWORD()
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        if not kernel32.WriteFile(self.h, buf, len(data), ctypes.byref(w),
                                  ctypes.byref(ov)):
            if ctypes.get_last_error() != ERROR_IO_PENDING:
                return -1
            if kernel32.WaitForSingleObject(self.ev, timeout_ms) != WAIT_OBJECT_0:
                kernel32.CancelIoEx(self.h, ctypes.byref(ov)); return -2
            if not kernel32.GetOverlappedResult(self.h, ctypes.byref(ov),
                                                ctypes.byref(w), False):
                return -3
        return w.value

    def read(self, size=20, timeout_ms=300):
        if not self.ok:
            return None
        ov = OVERLAPPED(); ov.hEvent = self.ev
        kernel32.ResetEvent(self.ev)
        g = wintypes.DWORD()
        buf = ctypes.create_string_buffer(size)
        if not kernel32.ReadFile(self.h, buf, size, ctypes.byref(g),
                                 ctypes.byref(ov)):
            if ctypes.get_last_error() != ERROR_IO_PENDING:
                return None
            if kernel32.WaitForSingleObject(self.ev, timeout_ms) != WAIT_OBJECT_0:
                kernel32.CancelIoEx(self.h, ctypes.byref(ov)); return None
            if not kernel32.GetOverlappedResult(self.h, ctypes.byref(ov),
                                                ctypes.byref(g), False):
                return None
        return buf.raw[:g.value]

    def drain(self, timeout_ms=40):
        for _ in range(6):
            if self.read(20, timeout_ms) is None:
                break

    def close(self):
        if self.ev:
            kernel32.CloseHandle(self.ev); self.ev = None
        if self.h and self.h != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self.h)
        self.h = None
        self.ok = False


# ---------------------------------------------------------------- 协议封装
RECEIVER_DEV = 0xFF
DEVICE_SLOT = 0x01          # 实测 GPW2 在槽位 1
UNIFIED_BATTERY = 0x1004
SHORT_SIZE, LONG_SIZE = 7, 20

# HID++ 电量状态字节 (0x1004 get_battery_info byte2)
BATTERY_STATES = {
    0x00: "放电中", 0x01: "充电中", 0x02: "充电将满", 0x03: "已充满",
    0x04: "慢速充电", 0x05: "电池无效", 0x06: "过热", 0x07: "充电错误",
}

# 0x1004 粗粒度电量档位 (byte1)
BATTERY_LEVELS = {0x01: "极低", 0x02: "偏低", 0x04: "良好", 0x08: "满电"}


class Gpw2Battery:
    """读取 046D:C547 接收器下配对设备的电量。"""

    def __init__(self, vid=0x046D, pid=0xC547, slot=DEVICE_SLOT):
        self.vid, self.pid, self.slot = vid, pid, slot
        self.short = None      # col01 句柄（1.0 寄存器）
        self.long = None       # col02 句柄（2.0 特性）
        self.battery_index = None
        self.last_error = None
        self.capabilities = None
        self.device_name = None

    # -- 生命周期 --
    def open(self):
        sp, lp = find_channels(self.vid, self.pid)
        if not lp:
            self.last_error = "未找到接收器接口（046D:C547 未插入？）"
            return False
        self.long = HidDevice(lp)
        if not self.long.ok:
            self.last_error = f"打开长报文通道失败 err={ctypes.get_last_error()}"
            return False
        if sp:
            self.short = HidDevice(sp)
        self.last_error = None
        return True

    def close(self):
        if self.long:
            self.long.close(); self.long = None
        if self.short:
            self.short.close(); self.short = None

    def __enter__(self):
        self.open(); return self

    def __exit__(self, *a):
        self.close()

    # -- 底层收发 --
    def _raw(self, dev, payload, wait=0.4):
        """发一条长报文，等响应；无响应返回 None（不做重试）。"""
        if not self.long:
            return None
        payload = bytes(payload)[:18].ljust(18, b"\x00")
        self.long.drain(40)
        if self.long.write(bytes([0x11, dev]) + payload, 500) < 0:
            return None
        deadline = time.time() + wait
        while time.time() < deadline:
            r = self.long.read(32, 150)
            if not r or r[0] not in (0x10, 0x11):
                continue
            if len(r) > 1 and r[1] != dev:
                continue
            return r
        return None

    def _call(self, payload, dev, tries=4, wait=0.4):
        """发长报文并匹配响应；设备休眠时先 ping 唤醒再重试。

        鼠标闲置若干分钟会挂起，此时所有 2.0 请求静默。先发 root.ping
        唤醒（响应本身不用），再重试真正的请求。
        """
        for attempt in range(tries):
            r = self._raw(dev, payload, wait)
            if r is not None:
                return r
            # 唤醒：root(0x00) fn1 = ping，参数任意
            self._raw(dev, bytes([0x00, 0x10, 0x00]), wait=0.3)
            time.sleep(0.15 * (attempt + 1))
        return None

    def _h2(self, index, function, *params):
        payload = bytes([index, (function << 4)]) + bytes(params)
        return self._call(payload, self.slot)

    # -- 业务 --
    def resolve_battery_feature(self):
        """root.getFeature(0x1004) -> feature_index"""
        r = self._call(bytes([0x00, 0x00, (UNIFIED_BATTERY >> 8) & 0xFF,
                              UNIFIED_BATTERY & 0xFF]), self.slot)
        if not r or len(r) < 5 or r[2] == 0x8F:
            self.last_error = f"查特性 0x{UNIFIED_BATTERY:04X} 失败"
            return None
        idx = r[4]
        if idx == 0:
            self.last_error = "设备未实现 UNIFIED_BATTERY 特性"
            return None
        self.battery_index = idx
        return idx

    def read_battery(self):
        """读电量。

        规范 (OpenLogi x1004-unifiedBattery):
            fn0 get_battery_capabilities -> byte0 级别支持位, byte1 rechargeable/percentage
            fn1 get_battery_info         -> byte0 charging_percentage,
                                            byte1 level(1/2/4/8),
                                            byte2 status(0..7),
                                            byte3 external_power
        设备休眠时无响应 -> 返回 None（调用方提示用户动一下鼠标）。
        """
        if self.battery_index is None:
            if self.resolve_battery_feature() is None:
                return None

        # 先读能力位，确认 charging_percentage 是真实百分比
        has_pct = None
        caps = self._h2(self.battery_index, 0x00)
        if caps and len(caps) >= 6 and caps[2] != 0x8F:
            has_pct = bool(caps[5] & 0x02)
            self.capabilities = {"levels_byte": caps[4], "flags_byte": caps[5],
                                 "rechargeable": bool(caps[5] & 0x01),
                                 "has_percentage": has_pct}

        r = self._h2(self.battery_index, 0x01)
        if not r or len(r) < 7 or r[2] == 0x8F:
            self.last_error = "读电量失败（设备可能休眠，请动一下鼠标）"
            return None
        pct, lvl, st = r[4], r[5], r[6]
        return {
            "percent": pct if has_pct is not False else None,
            "charging": st in (0x01, 0x02, 0x04),
            "level": lvl,
            "level_text": BATTERY_LEVELS.get(lvl, f"0x{lvl:02X}"),
            "state": st,
            "state_text": BATTERY_STATES.get(st, f"0x{st:02X}"),
            "has_percentage": has_pct,
            "raw": r.hex(" "),
        }

    def read_firmware(self):
        """接收器固件版本（1.0 寄存器 0xF1）——用于自检。"""
        if not self.short:
            return None
        self.short.drain(40)
        self.short.write(bytes([0x10, RECEIVER_DEV, 0x81, 0xF1, 0x01, 0x00, 0x00]), 400)
        r = self.short.read(7, 400)
        if r and len(r) >= 5 and r[3] == 0xF1:
            return f"{r[4]:02X}.{r[5]:02X}"
        return None

    def read_connection_count(self):
        """接收器报告的已连接设备数（1.0 寄存器 0x02，响应 byte4）。

        Lightspeed 单设备接收器有时恒为 0（设备走直连而非 Unifying 配对表），
        此时返回 None 表示不可用，调用方不应据此判断设备在线与否。
        """
        if not self.short:
            return None
        self.short.drain(40)
        self.short.write(bytes([0x10, RECEIVER_DEV, 0x81, 0x02, 0x00, 0x00, 0x00]), 400)
        r = self.short.read(7, 400)
        if r and len(r) >= 5 and r[2] != 0x8F and r[3] == 0x02:
            return r[4]
        return None