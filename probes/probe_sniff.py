"""定音实验：抓到鼠标数据的接口是哪个。

移动鼠标时，哪个 HID 接口有 Input Report 在流，鼠标就走哪条链路。
同时统计各接口数据量，找出真正的鼠标通道。
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import threading
import time

from ctypes import wintypes

import probe_channels as pc

setupapi = pc.setupapi
hid = pc.hid
kernel32 = pc.kernel32
GUID_DEVINTERFACE_HID = pc.GUID_DEVINTERFACE_HID
GUID = pc.GUID
SP_DEVICE_INTERFACE_DATA = pc.SP_DEVICE_INTERFACE_DATA
HIDD_ATTRIBUTES = pc.HIDD_ATTRIBUTES
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x02, 0x10
OPEN_EXISTING = 3
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING, WAIT_OBJECT_0 = 997, 0

class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]

kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED),
                                         ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]

class Reader:
    def __init__(self, path, size):
        self.path = path
        self.size = size
        self.count = 0
        self.samples = []
        self.stop = False
        self.thread = None
        self.err = None

    def run(self):
        try:
            h = kernel32.CreateFileW(self.path, GENERIC_READ, 3, None,
                                     OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
            if h == INVALID_HANDLE_VALUE:
                self.err = f"open err={ctypes.get_last_error()}"
                return
            ev = kernel32.CreateEventW(None, True, False, None)
            while not self.stop:
                ov = OVERLAPPED(); ov.hEvent = ev
                kernel32.ResetEvent(ev)
                got = wintypes.DWORD()
                buf = ctypes.create_string_buffer(self.size)
                ok = kernel32.ReadFile(h, buf, self.size, ctypes.byref(got),
                                       ctypes.byref(ov))
                if not ok:
                    if ctypes.get_last_error() != ERROR_IO_PENDING:
                        time.sleep(0.02); continue
                    if kernel32.WaitForSingleObject(ev, 300) != WAIT_OBJECT_0:
                        kernel32.CancelIoEx(h, ctypes.byref(ov)); continue
                    if not kernel32.GetOverlappedResult(h, ctypes.byref(ov),
                                                        ctypes.byref(got), False):
                        continue
                if got.value:
                    self.count += 1
                    if len(self.samples) < 6:
                        self.samples.append(buf.raw[:got.value].hex(" "))
            kernel32.CloseHandle(ev)
            kernel32.CloseHandle(h)
        except Exception as e:
            self.err = repr(e)

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

def all_hid_interfaces():
    guid = GUID(GUID_DEVINTERFACE_HID)
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    out = []
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
        f = kernel32.CreateFileW(path, 0, 3, None, OPEN_EXISTING, 0, None)
        if f == INVALID_HANDLE_VALUE:
            continue
        a = HIDD_ATTRIBUTES()
        a.Size = ctypes.sizeof(a)
        ok = hid.HidD_GetAttributes(f, ctypes.byref(a))
        kernel32.CloseHandle(f)
        if ok:
            out.append((path, f"{a.VendorID:04X}:{a.ProductID:04X}"))
    setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out

def main():
    ifs = all_hid_interfaces()
    print(f"共 {len(ifs)} 个 HID 接口，全部开始监听\n")
    readers = []
    for path, vid in ifs:
        tag = path.split("#")[1] if "#" in path else path
        r = Reader(path, 32)
        r.tag = f"{vid} {tag[:44]}"
        r.start()
        readers.append(r)

    print(">>> 现在开始，请连续移动鼠标 10 秒 <<<")
    for i in range(10, 0, -1):
        print(f"  倒计时 {i}...", end="\r", flush=True)
        time.sleep(1)
    print()
    for r in readers:
        r.stop = True
    time.sleep(0.5)

    print("\n" + "=" * 78)
    print("各接口数据统计（10 秒内收到的 Input Report 条数）")
    print("=" * 78)
    readers.sort(key=lambda x: -x.count)
    for r in readers:
        mark = "  <<< 有数据!" if r.count else ""
        err = f"  [{r.err}]" if r.err else ""
        print(f"  {r.count:>5} 条  {r.tag}{mark}{err}")
        for s in r.samples[:3]:
            print(f"           {s}")

    print("\n" + "=" * 78)
    print("判据：有数据的接口 = 鼠标实际链路")
    print("=" * 78)
    live = [r for r in readers if r.count > 0]
    logi = [r for r in live if "046D" in r.tag]
    if logi:
        print("罗技接口中有数据的:")
        for r in logi:
            print(f"  {r.tag}  ({r.count} 条)")
    else:
        print("没有任何罗技接口收到数据")
        if live:
            print("有数据的是: " + ", ".join(r.tag.split()[0] for r in live))
    return 0

if __name__ == "__main__":
    sys.exit(main())