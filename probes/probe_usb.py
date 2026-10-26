"""从 USB / 系统层深挖真实设备身份与配对状态。

不再依赖猜 HID++，改查：
  1. 该接收器的 USB 描述符（真实 iProduct / iManufacturer / 序列号）
  2. 它的父设备与所有兄弟节点
  3. Windows 是否给它挂了电量接口
  4. 设备实例路径里的唯一 ID（用于定位）
"""
import ctypes
import subprocess
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctypes import wintypes

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

DIGCF_PRESENT, DIGCF_DEVICEINTERFACE, DIGCF_ALLCLASSES = 0x02, 0x10, 0x04
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

class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID),
                ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]

setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                          wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(SP_DEVINFO_DATA)]
setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD)]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

SPDRP_DEVICEDESC, SPDRP_HARDWAREID, SPDRP_FRIENDLYNAME = 0x00, 0x01, 0x0C
SPDRP_MFG, SPDRP_CLASS, SPDRP_CLASSGUID = 0x0B, 0x07, 0x0A

def get_str(hdev, did, prop):
    buf = ctypes.create_unicode_buffer(1024)
    n = wintypes.DWORD()
    if setupapi.SetupDiGetDeviceRegistryPropertyW(
            hdev, ctypes.byref(did), prop, None, buf, ctypes.sizeof(buf),
            ctypes.byref(n)):
        return buf.value
    return ""

def get_multi(hdev, did, prop):
    buf = ctypes.create_string_buffer(4096)
    n = wintypes.DWORD()
    if setupapi.SetupDiGetDeviceRegistryPropertyW(
            hdev, ctypes.byref(did), prop, None, buf, ctypes.sizeof(buf),
            ctypes.byref(n)):
        parts = buf.raw[:n.value].decode("utf-16-le", "ignore").split("\x00")
        return [p for p in parts if p]
    return []

def main():
    print("=== 用 PowerShell 查罗技相关设备的完整信息 ===\n")
    ps = r"""
$devs = Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object {
    $_.InstanceId -like '*046D*' -or $_.InstanceId -like '*VID_046D*'
}
foreach ($d in $devs) {
    Write-Output "----------------------------------------"
    Write-Output ("Class      : " + $d.Class)
    Write-Output ("FriendlyNam: " + $d.FriendlyName)
    Write-Output ("Status     : " + $d.Status)
    Write-Output ("InstanceId : " + $d.InstanceId)
    Write-Output ("Problem    : " + $d.Problem)
}
"""
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout or "(空)")

    print("\n=== 查接收器的电量相关属性 ===")
    ps2 = r"""
$root = "HID\VID_046D&PID_C547&MI_00\8&22172614&0&0000"
$d = Get-PnpDevice -InstanceId $root -ErrorAction SilentlyContinue
if ($d) {
    Write-Output ("找到: " + $d.FriendlyName)
    $keys = @("{10497B1B-2EE0-4E18-B9FB-3D9D0B3B2B7B} 3","DEVPKEY_Device_BatteryLevel")
    $props = Get-PnpDeviceProperty -InstanceId $root -ErrorAction SilentlyContinue
    foreach ($p in $props) {
        if ($p.KeyName -match 'Battery|Power|Batt') {
            Write-Output ($p.KeyName + " = " + $p.Data)
        }
    }
} else { Write-Output "未找到设备实例" }
Write-Output "---- 全部可能的电量属性 ----"
Get-PnpDevice -Class 'HIDClass' -ErrorAction SilentlyContinue | ForEach-Object {
    $inst = $_.InstanceId
    $ps = Get-PnpDeviceProperty -InstanceId $inst -ErrorAction SilentlyContinue |
          Where-Object { $_.KeyName -match 'Battery' }
    if ($ps) {
        Write-Output ("[" + $inst + "]")
        foreach ($p in $ps) { Write-Output ("   " + $p.KeyName + " = " + $p.Data) }
    }
}
"""
    r2 = subprocess.run(["powershell", "-NoProfile", "-Command", ps2],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r2.stdout or "(空)")
    if r2.stderr:
        print("stderr:", r2.stderr[:500])

    print("\n=== SetupAPI 层：所有 HIDClass 设备的 HardwareID ===")
    guid = GUID("{745A17A0-74D3-11D0-B6FE-00A0C90F57DA}")  # HIDClass GUID
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, DIGCF_PRESENT)
    if hdev != ctypes.c_void_p(-1).value:
        i = 0
        while True:
            did = SP_DEVINFO_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInfo(hdev, i, ctypes.byref(did)):
                break
            i += 1
            desc = get_str(hdev, did, SPDRP_DEVICEDESC)
            friendly = get_str(hdev, did, SPDRP_FRIENDLYNAME)
            hwids = get_multi(hdev, did, SPDRP_HARDWAREID)
            if any("046D" in x.upper() or "C547" in x.upper() for x in hwids):
                print(f"  desc={desc!r} friendly={friendly!r}")
                for x in hwids:
                    print(f"      HWID: {x}")
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return 0

if __name__ == "__main__":
    sys.exit(main())