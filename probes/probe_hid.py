import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""实机探测：枚举本机 HID 接口，找 GPW2 接收器 0x046D:0xC547。纯标准库 + ctypes。"""
import ctypes
from ctypes import wintypes

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

GUID_DEVINTERFACE_HID = "{4D1E55B2-F16F-11CF-88CB-001111000030}"

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, s):
        super().__init__()
        parts = s.strip("{}").split("-", 3)
        self.Data1 = int(parts[0], 16)
        self.Data2 = int(parts[1], 16)
        self.Data3 = int(parts[2], 16)
        for i, b in enumerate(bytes.fromhex(parts[3].replace("-", ""))):
            self.Data4[i] = b

class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]

class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]

class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]

class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
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

# --- SetupAPI bindings ---
setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(GUID),
    wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(SP_DEVINFO_DATA),
]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]

hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_GetManufacturerString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]

kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x01
FILE_SHARE_WRITE = 0x02
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

SPDRP_DEVICEDESC = 0x00
SPDRP_FRIENDLYNAME = 0x0C

TARGET_VID = 0x046D
TARGET_PID = 0xC547

def open_hid(path, access):
    return kernel32.CreateFileW(
        path, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None,
    )

def get_prop(dev_info, did, prop):
    buf = ctypes.create_unicode_buffer(512)
    needed = wintypes.DWORD()
    ok = setupapi.SetupDiGetDeviceRegistryPropertyW(
        dev_info, ctypes.byref(did), prop, None, buf,
        ctypes.sizeof(buf), ctypes.byref(needed),
    )
    return buf.value if ok else ""

def main():
    guid = GUID(GUID_DEVINTERFACE_HID)
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )
    if hdev == INVALID_HANDLE_VALUE:
        print("SetupDiGetClassDevs failed:", ctypes.get_last_error())
        return

    idx = 0
    hits = []
    print(f"{'VID:PID':>12}  {'Usage':>10}  {'Tgt?':>4}  {'In':>4} {'Out':>4} {'Feat':>4}  Product / Path")
    print("-" * 118)

    try:
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                hdev, None, ctypes.byref(guid), idx, ctypes.byref(did)
            ):
                break
            idx += 1

            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(devinfo)
            needed = wintypes.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), None, 0, ctypes.byref(needed), None
            )
            if not needed.value:
                continue
            buf = ctypes.create_string_buffer(needed.value)
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8  # cbSize on 64-bit
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), buf, needed.value,
                ctypes.byref(needed), ctypes.byref(devinfo),
            ):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)

            # 先试读写，失败再降到 access=0（系统独占的顶层集合）
            h = open_hid(path, 0)
            access_used = 0
            if h == INVALID_HANDLE_VALUE:
                continue

            attrs = HIDD_ATTRIBUTES()
            attrs.Size = ctypes.sizeof(attrs)
            if not hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
                kernel32.CloseHandle(h)
                continue

            pp = ctypes.c_void_p()
            usage = usage_page = 0
            caps = HIDP_CAPS()
            if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                if hid.HidP_GetCaps(pp, ctypes.byref(caps)):
                    usage = caps.Usage
                    usage_page = caps.UsagePage
                hid.HidD_FreePreparsedData(pp)

            pbuf = ctypes.create_unicode_buffer(256)
            product = ""
            if hid.HidD_GetProductString(h, pbuf, ctypes.sizeof(pbuf)):
                product = pbuf.value

            target = attrs.VendorID == TARGET_VID and attrs.ProductID == TARGET_PID
            desc = get_prop(hdev, devinfo, SPDRP_DEVICEDESC)

            flag = " <<<" if target else ""
            print(f"  {attrs.VendorID:04X}:{attrs.ProductID:04X}  "
                  f"{usage_page:04X}/{usage:04X}  {str(target):>5}  "
                  f"{caps.InputReportByteLength:>4} {caps.OutputReportByteLength:>4} "
                  f"{caps.FeatureReportByteLength:>4}  {product or desc}{flag}")

            if target:
                hits.append((path, caps, product, desc, usage, usage_page))
            kernel32.CloseHandle(h)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)

    print()
    print(f"HID 接口总数: {idx}")
    print(f"匹配 {TARGET_VID:04X}:{TARGET_PID:04X} 的接口: {len(hits)}")
    for p, caps, prod, desc, usage, up in hits:
        print("  路径:", p)
        print(f"    product={prod!r} desc={desc!r} usage={up:04X}/{usage:04X} "
              f"featLen={caps.FeatureReportByteLength}")

if __name__ == "__main__":
    main()