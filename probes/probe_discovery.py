"""探测：动态接收器发现 + 槽位扫描 + DEVICE_NAME 读取。

为「自动检测连入的 2.4G 设备并匹配」验证三个协议前提（2026-10-27）:
  1. 不靠 PID 白名单：对 046D 全部 VID:PID 的接口试 1.0 固件寄存器交换
     (10 FF 81 F1)，能应答的接口 = 接收器的短报文通道（键盘/鼠标本体打不开
     或不应答，天然被排除）
  2. 槽位扫描：col02 逐槽位 0x01..0x06 发 root.ping，看在位槽位怎么回、
     空槽怎么回（错误码 or 静默）—— 生产代码的在线/空槽/休眠判据从这里来
  3. DEVICE_NAME(0x0003)：getFeature 拿 index，fn0 getCount + fn1 读取，
     确认真实设备名的报文格式

只读设备，不写任何东西，可安全反复运行。需要接收器在线。
"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hidpp import (HidDevice, GUID, GUID_DEVINTERFACE_HID, HIDD_ATTRIBUTES,
                   SP_DEVICE_INTERFACE_DATA, DIGCF_PRESENT, DIGCF_DEVICEINTERFACE,
                   OPEN_EXISTING, INVALID_HANDLE_VALUE, setupapi, hid, kernel32,
                   wintypes)

ROOT_IDX = 0x00


def list_all_hid_interfaces():
    """无 VID/PID 过滤，枚举本机全部 HID 接口，返回 [(path, vid, pid)]。"""
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
            if ok:
                out.append((path, a.VendorID, a.ProductID))
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


def try_firmware_exchange(path):
    """对单个接口试 1.0 固件寄存器交换。应答 → (固件串, HidDevice)。"""
    d = HidDevice(path)
    if not d.ok:
        return None, None
    try:
        d.drain(30)
        d.write(bytes([0x10, 0xFF, 0x81, 0xF1, 0x01, 0x00, 0x00]), 400)
        r = d.read(7, 400)
        if r and len(r) >= 6 and r[0] == 0x10 and r[1] == 0xFF \
                and r[2] == 0x81 and r[3] == 0xF1:
            return f"{r[4]:02X}.{r[5]:02X}", d
    except Exception as e:                                  # noqa: BLE001
        print(f"    (异常 {e})")
    d.close()
    return None, None


def h20_call(dev_h, dev, payload, wait=0.35, tries=3):
    """长报文收发：dev + payload(不含 report id / dev)。返回原始响应或 None。

    ⚠ 总长必须 = 20 字节（col02 的 OutputReportByteLength），少一字节 write 必败。
    """
    for _ in range(tries):
        dev_h.drain(30)
        data = bytes(payload)[:18].ljust(18, b"\x00")
        if dev_h.write(bytes([0x11, dev]) + data, 500) < 0:
            time.sleep(0.1)
            continue
        deadline = time.time() + wait
        while time.time() < deadline:
            r = dev_h.read(32, 120)
            if not r or r[0] != 0x11 or len(r) < 4:
                continue
            if r[1] != dev:
                continue
            return r
        time.sleep(0.1)
    return None


def get_feature(dev_h, dev, fid):
    r = h20_call(dev_h, dev, [ROOT_IDX, 0x00, (fid >> 8) & 0xFF, fid & 0xFF])
    if not r or len(r) < 5 or 0x8F in (r[2], r[3]):
        return None
    return r[4]


def main():
    print("=" * 70)
    print("探测 1：无过滤枚举 HID 接口 → 找 046D 接收器")
    print("=" * 70)
    ifs = list_all_hid_interfaces()
    groups = {}
    for path, vid, pid in ifs:
        groups.setdefault((vid, pid), []).append(path)
    logi = {vp: ps for vp, ps in groups.items() if vp[0] == 0x046D}
    others = {vp: len(ps) for vp, ps in groups.items() if vp[0] != 0x046D}
    print(f"全部 HID 接口 {len(ifs)} 个，VID:PID 分组 {len(groups)} 种")
    print(f"  其它厂商: {others}")
    for vp, ps in sorted(logi.items()):
        print(f"  046D:{vp[1]:04X}  接口数 {len(ps)}")

    print()
    print("=" * 70)
    print("探测 2：对每个 046D:PID 的接口试固件寄存器交换（识别接收器）")
    print("=" * 70)
    receivers = []      # (vid, pid, short_path, short_dev, fw)
    for vp, paths in sorted(logi.items()):
        vid, pid = vp
        for p in paths:
            tag = p.split("#")[-1] if "#" in p else p[-40:]
            fw, d = try_firmware_exchange(p)
            if fw:
                print(f"  ✅ 046D:{pid:04X} 接收器  {tag}  固件 {fw}")
                receivers.append((vid, pid, p, d))
            else:
                d.close() if d else None
                print(f"  -- 046D:{pid:04X} 非接收器 {tag}")
    if not receivers:
        print("未发现任何罗技接收器 —— 请插入接收器后重跑")
        return 1

    print()
    print("=" * 70)
    print("探测 3：槽位扫描（col02 逐槽位 root.ping）")
    print("=" * 70)
    for vid, pid, short_path, short in receivers:
        # 找 col02
        long_path = None
        for path, v2, p2 in ifs:
            if v2 == vid and p2 == pid and "mi_02" in path.lower() \
                    and "col02" in path.lower():
                long_path = path
                break
        if not long_path:
            print(f"  046D:{pid:04X}: 未找到 col02 长报文通道，跳过")
            short.close()
            continue
        long = HidDevice(long_path)
        if not long.ok:
            print(f"  046D:{pid:04X}: col02 打开失败")
            short.close()
            continue

        conn = None
        short.drain(30)
        short.write(bytes([0x10, 0xFF, 0x81, 0x02, 0x00, 0x00, 0x00]), 400)
        r = short.read(7, 400)
        if r and len(r) >= 5 and r[2] != 0x8F and r[3] == 0x02:
            conn = r[4]
        print(f"  接收器 046D:{pid:04X} 固件可读，连接数寄存器 = {conn}")

        online = []
        for slot in range(1, 7):
            r = h20_call(long, slot, [ROOT_IDX, 0x01, 0x5A], tries=2, wait=0.3)
            if r is None:
                print(f"  槽位 {slot}: 静默（空槽或设备休眠）")
                continue
            if 0x8F in (r[2], r[3]):
                print(f"  槽位 {slot}: 错误响应 {r.hex(' ')}")
                continue
            print(f"  槽位 {slot}: ✅ ping 应答  {r.hex(' ')}")
            online.append(slot)

        print()
        print("=" * 70)
        print("探测 4：在线槽位读 DEVICE_NAME / UNIFIED_BATTERY")
        print("=" * 70)
        for slot in online:
            idx_name = get_feature(long, slot, 0x0003)
            idx_bat = get_feature(long, slot, 0x1004)
            print(f"  槽位 {slot}: DEVICE_NAME idx={idx_name and hex(idx_name)}"
                  f"  UNIFIED_BATTERY idx={idx_bat and hex(idx_bat)}")
            if idx_name:
                # fn0 getCount
                r = h20_call(long, slot, [idx_name, 0x00])
                count = r[4] if r and len(r) > 4 and 0x8F not in (r[2], r[3]) else None
                print(f"    fn0 getCount 响应: {(r.hex(' ') if r else None)}"
                      f"  → 名字长度 {count}")
                # fn1 getDeviceName(offset)
                name = b""
                for off in (0, 11):
                    r = h20_call(long, slot, [idx_name, 0x10, off])
                    if not r or 0x8F in (r[2], r[3]):
                        print(f"    fn1(offset={off}) 响应: {r.hex(' ') if r else None}")
                        continue
                    print(f"    fn1(offset={off}) 响应: {r.hex(' ')}")
                    name += bytes(r[4:])
                if count:
                    name = name[:count]
                printable = "".join(chr(c) if 32 <= c < 127 else "." for c in name)
                print(f"    → 设备名: \"{printable}\"")
            if idx_bat:
                r = h20_call(long, slot, [idx_bat, 0x10])
                if r and 0x8F not in (r[2], r[3]):
                    print(f"    电量: {r[4]}%  状态 0x{r[6]:02X}  raw={r.hex(' ')}")
                else:
                    print(f"    电量读取失败: {r.hex(' ') if r else '静默'}")
        long.close()
        short.close()

    print()
    print("探测完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
