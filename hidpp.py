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
def list_interfaces(vid=0x046D, pid=None):
    """返回该 VID 下所有 HID 接口路径；pid 为 None 时不按 PID 过滤。

    按 (mi_XX, colNN) 排序。
    """
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
            if ok and a.VendorID == vid and (pid is None or a.ProductID == pid):
                out.append(path)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


def find_channels(vid=0x046D, pid=0xC547):
    """定位 (col01 短报文句柄路径, col02 长报文句柄路径)。

    集合名以 `&` 分隔，用 `&col02` 锚定集合段，避免子串误配。
    """
    short_path = long_path = None
    for p in list_interfaces(vid, pid):
        low = p.lower()
        if "mi_02" not in low:
            continue
        if "col01" in low and short_path is None:
            short_path = p
        elif "&col02" in low and long_path is None:
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
LOGITECH_VID = 0x046D      # 发现阶段只认厂商号，PID 靠协议探测自动识别
UNIFIED_BATTERY = 0x1004
DEVICE_NAME_ID = 0x0003    # 部分设备上返回的是序列号等信息，仅作回退
FRIENDLY_NAME_ID = 0x0005  # 实测 GPW2 -> "PRO X SUPERLIGHT 2 SE"
ROOT_INDEX = 0x00
SLOT_FIRST, SLOT_LAST = 1, 6
SHORT_SIZE, LONG_SIZE = 7, 20

# HID++ 电量状态字节 (0x1004 get_battery_info byte2)
BATTERY_STATES = {
    0x00: "放电中", 0x01: "充电中", 0x02: "充电将满", 0x03: "已充满",
    0x04: "慢速充电", 0x05: "电池无效", 0x06: "过热", 0x07: "充电错误",
}

# 0x1004 粗粒度电量档位 (byte1)
BATTERY_LEVELS = {0x01: "极低", 0x02: "偏低", 0x04: "良好", 0x08: "满电"}


class ReceiverBattery:
    """读取一个罗技接收器下配对设备的电量。

    与旧 Gpw2Battery 的区别：不预设 PID 与槽位 —— 接收器接口由
    discover_receivers() 按协议确认，槽位由 scan() 逐个探测。
    """

    def __init__(self, vid=0x046D, pid=0xC547, slot=0x01):
        self.vid, self.pid, self.slot = vid, pid, slot
        self.short = None      # col01 句柄（1.0 寄存器）
        self.long = None       # col02 句柄（2.0 特性）
        self.battery_index = None
        self.last_error = None
        self.capabilities = None
        self.device_name = None
        self.online_slots = []     # scan_receiver 填充：本接收器全部在线槽位
        self._names = {}           # 槽位 -> 设备名（名字随槽位而变）

    # -- 生命周期 --
    def open(self):
        if self.pid:
            sp, lp = find_channels(self.vid, self.pid)
        else:
            # PID 未知：按 VID 走协议发现，顺手补全 PID
            sp = lp = None
            for vid_, pid_, sp_, lp_, _fw in discover_receivers(self.vid):
                if vid_ == self.vid:
                    sp, lp, self.pid = sp_, lp_, pid_
                    break
        if not lp:
            self.last_error = f"未找到接收器接口（{self.vid:04X} 未插入？）"
            return False
        self.long = HidDevice(lp)
        if not self.long.ok:
            self.last_error = f"打开长报文通道失败 err={ctypes.get_last_error()}"
            return False
        if sp:
            self.short = HidDevice(sp)
        self.last_error = None
        return True

    def open_paths(self, short_path, long_path):
        """发现流程用：直接拿已确认的接口路径开句柄。"""
        if not long_path:
            self.last_error = "接收器缺少 col02 长报文通道"
            return False
        self.long = HidDevice(long_path)
        if not self.long.ok:
            self.last_error = f"打开长报文通道失败 err={ctypes.get_last_error()}"
            return False
        if short_path:
            self.short = HidDevice(short_path)
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
        if not r or len(r) < 5 or r[2] in (0x8F, 0xFF):
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
        if caps and len(caps) >= 6 and caps[2] not in (0x8F, 0xFF):
            has_pct = bool(caps[5] & 0x02)
            self.capabilities = {"levels_byte": caps[4], "flags_byte": caps[5],
                                 "rechargeable": bool(caps[5] & 0x01),
                                 "has_percentage": has_pct}

        r = self._h2(self.battery_index, 0x01)
        if not r or len(r) < 7 or r[2] in (0x8F, 0xFF):
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
        if r and len(r) >= 5 and r[2] not in (0x8F, 0xFF) and r[3] == 0x02:
            return r[4]
        return None

    def read_device_name(self):
        """读配对设备的名字：优先 FRIENDLY_NAME(0x0005)，回退 DEVICE_NAME(0x0003)。

        实测 GPW2：0x0005 fn0 -> 长度，fn1(块偏移) -> ASCII 名字
        （"PRO X SUPERLIGHT 2 SE"）；0x0003 在该设备上是序列号等信息，不是名字。
        块大小 16 字节（fn1 参数是块偏移，不是字节偏移——实测 11/16 才分块正确）。
        """
        got = self._names.get(self.slot)
        if got:
            return got
        for fid in (FRIENDLY_NAME_ID, DEVICE_NAME_ID):
            r = self._call(bytes([0x00, 0x00, (fid >> 8) & 0xFF, fid & 0xFF]), self.slot)
            if not r or len(r) < 5 or r[2] in (0x8F, 0xFF):
                continue
            idx = r[4]
            if idx == 0:
                continue
            total = None
            r0 = self._h2(idx, 0x00)
            if r0 and len(r0) > 4 and r0[2] not in (0x8F, 0xFF):
                total = r0[4]
            if not total:
                continue
            chunks = []
            for off in range(0, min(total, 64), 16):
                rc = self._h2(idx, 0x01, off)
                if not rc or len(rc) < 5 or rc[2] in (0x8F, 0xFF):
                    break
                chunks.append(bytes(rc[4:4 + min(16, total - off)]))
            if not chunks:
                continue
            name = b"".join(chunks)[:total].decode("ascii", "replace").strip("\x00 ")
            if name and any(c.isalnum() for c in name):
                self._names[self.slot] = name
                self.device_name = name
                return name
        return None


# 旧名兼容：外部代码 / 验收脚本仍在用
Gpw2Battery = ReceiverBattery


# ---------------------------------------------------------------- 动态发现
def _firmware_exchange(short_dev):
    """1.0 固件寄存器交换（10 FF 81 F1）。应答 -> 固件串，否则 None。"""
    short_dev.drain(30)
    short_dev.write(bytes([0x10, RECEIVER_DEV, 0x81, 0xF1, 0x01, 0x00, 0x00]), 400)
    r = short_dev.read(7, 400)
    if r and len(r) >= 6 and r[0] == 0x10 and r[1] == RECEIVER_DEV \
            and r[2] == 0x81 and r[3] == 0xF1:
        return f"{r[4]:02X}.{r[5]:02X}"
    return None


def discover_receivers(vid=LOGITECH_VID):
    """无 PID 白名单发现罗技接收器。

    先把 mi_02 的 col01/col02 按「设备前缀」配对（同一接收器的两个集合共享
    `hid#vid..&mi_02` 前缀），再对 col01 试 1.0 固件寄存器交换：应答 =
    接收器（键盘/鼠标本体的接口要么打不开要么不应答，天然排除）。
    返回 [(vid, pid, short_path, long_path, firmware)]。
    """
    cols = {}
    for p in list_interfaces(vid, None):
        low = p.lower()
        if "mi_02" not in low:
            continue
        key = low.split("&col")[0]
        entry = cols.setdefault(key, [None, None])
        if "&col01" in low and entry[0] is None:
            entry[0] = p
        elif "&col02" in low and entry[1] is None:
            entry[1] = p
    out = []
    for key, (sp, lp) in sorted(cols.items()):
        if not sp:
            continue
        d = HidDevice(sp)
        if not d.ok:
            continue
        fw = _firmware_exchange(d)
        d.close()
        if fw is None:
            continue                      # 非接收器（键盘/鼠标本体等）
        pid = 0
        h = kernel32.CreateFileW(sp, 0, 3, None, OPEN_EXISTING, 0, None)
        if h != INVALID_HANDLE_VALUE:
            a = HIDD_ATTRIBUTES()
            a.Size = ctypes.sizeof(a)
            if hid.HidD_GetAttributes(h, ctypes.byref(a)):
                pid = a.ProductID
            kernel32.CloseHandle(h)
        out.append((vid, pid, sp, lp, fw))
    return out


def scan_slots(long_dev, first=SLOT_FIRST, last=SLOT_LAST, tries=3):
    """逐槽位 root.ping，返回 {slot: 响应或 None}。

    None = 静默（空槽或设备休眠）；错误响应（r[2]==0xFF）也记为在线——
    槽位有效但该功能不支持时设备仍在。
    设备刚睡时第一发 ping 只起唤醒作用（实测无应答），所以前几次重试；
    一旦有槽位应答说明设备已醒，后续槽位一次即可（空槽怎么等都不会答）。
    """
    slots = {}
    awake = False
    for slot in range(first, last + 1):
        r = None
        for attempt in range(tries if not awake else 1):
            long_dev.drain(30)
            payload = bytes([0x11, slot, ROOT_INDEX, 0x10, 0x5A]).ljust(20, b"\x00")
            if long_dev.write(payload, 500) >= 0:
                deadline = time.time() + 0.4
                while time.time() < deadline:
                    resp = long_dev.read(32, 120)
                    if not resp or resp[0] != 0x11 or len(resp) < 3:
                        continue
                    if resp[1] != slot:
                        continue
                    r = resp
                    break
            if r is not None:
                break
            time.sleep(0.15 * (attempt + 1))
        slots[slot] = r
        if r is not None:
            awake = True
    return slots


def scan_receiver(vid=None, pid=None, short_path=None, long_path=None):
    """发现 + 扫描一台接收器，返回电量读取器列表。

    scan_receiver()          全自动：发现全部接收器并逐台扫描
    scan_receiver(vid, pid)  只处理指定接收器（走 open()）
    scan_receiver(短/长路径) 直接用已确认的路径（discover 流程复用）
    """
    receivers = []
    found = []
    if short_path or long_path:
        own = ReceiverBattery(vid or 0, pid or 0, slot=0)
        if own.open_paths(short_path, long_path):
            receivers.append(own)
        else:
            return []
    elif vid is not None:
        own = ReceiverBattery(vid, pid or 0, slot=0)
        if own.open():
            receivers.append(own)
        else:
            return []
    else:
        for vid_, pid_, sp, lp, _fw in discover_receivers():
            own = ReceiverBattery(vid_, pid_, slot=0)
            if own.open_paths(sp, lp):
                receivers.append(own)

    for own in receivers:
        hits = [s for s, resp in scan_slots(own.long).items() if resp is not None]
        own.online_slots = hits
        if hits:
            own.slot = hits[0]           # 第一个在线槽位 = 工作槽位
            found.append(own)
        else:
            own.close()                  # 该接收器下没有在线设备
    return found