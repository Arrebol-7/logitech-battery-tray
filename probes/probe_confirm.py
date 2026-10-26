"""确认读数：连续多次读电量 + 读设备名 + 读所有已知电池相关特性。"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time

from probe_channels import Dev, list_vendor_paths, INVALID_HANDLE_VALUE

DEV = 0x01

def call(d, dev, payload, size=20, report_id=0x11, tries=5, wait=0.5):
    for _ in range(tries):
        msg = bytes([report_id, dev]) + bytes(payload)
        msg = msg + bytes(size - len(msg))
        d.drain(50)
        if d.write(msg[:size], 500) < 0:
            continue
        deadline = time.time() + wait
        while time.time() < deadline:
            r = d.read(32, 150)
            if not r or r[0] not in (0x10, 0x11):
                continue
            if len(r) > 1 and r[1] != dev:
                continue
            return r
        time.sleep(0.12)
    return None

def h2(d, dev, idx, fn, *params):
    payload = bytes([idx, (fn << 4) | 0x00]) + bytes(params)
    payload = payload[:18].ljust(18, b"\x00")
    return call(d, dev, payload)

def main():
    paths = list_vendor_paths()
    col02 = next((p for p in paths if "col02" in p.lower()), None)
    d = Dev(col02)
    if d.h == INVALID_HANDLE_VALUE:
        print("打开失败"); return 1

    print("=" * 74)
    print("A. 连续 8 次读 UNIFIED_BATTERY(0x1004, idx=0x06).fn0")
    print("=" * 74)
    vals = []
    for i in range(8):
        r = h2(d, DEV, 0x06, 0x00)
        if r and len(r) >= 7:
            b0, b1, st = r[4], r[5], r[6]
            lvl = b0 & 0x7F
            chg = bool(b0 >> 7)
            vals.append(lvl)
            states = {0x00: "放电", 0x01: "充电", 0x02: "充电完成",
                      0x03: "充电中(效率)", 0x04: "充电完成", 0x05: "错误",
                      0x06: "慢速充电", 0x07: "无效"}
            print(f"  #{i+1} raw={r[:9].hex(' ')}  -> 电量={lvl}% 充电字节=0x{b0:02X} "
                  f"next={b1 & 0x7F}% 状态=0x{st:02X}({states.get(st,'?')})")
        else:
            print(f"  #{i+1} 无响应 ({r.hex(' ') if r else 'None'})")
        time.sleep(0.25)
    if vals:
        print(f"\n  读数序列: {vals}  最小={min(vals)} 最大={max(vals)}")

    print("\n" + "=" * 74)
    print("B. 读设备名 (0x0003 DEVICE_NAME, fn0 getCount / fn1 getString)  & 其他身份")
    print("=" * 74)
    # 先拿 DEVICE_NAME 的 index
    r = call(d, DEV, bytes([0x00, 0x00, 0x00, 0x03]).ljust(18, b"\x00"))
    idx = r[4] if r and len(r) > 4 else 0x02
    print(f"  DEVICE_NAME(0x0003) index = 0x{idx:02X}")
    r = h2(d, DEV, idx, 0x00)   # getCount
    print(f"  fn0 getCount  -> {r[:6].hex(' ') if r else 'None'}")
    n = r[4] if r and len(r) > 4 else 0
    if 0 < n < 64:
        r = h2(d, DEV, idx, 0x10)  # getString? 
        print(f"  fn1 getString -> {r[:40].hex(' ') if r else 'None'}")
        if r and len(r) > 4:
            raw = bytes(r[4:4 + n])
            print(f"      名称原文 = {raw!r}")
            print(f"      解码 = {raw.decode('utf-8', 'replace')}")

    print("\n" + "=" * 74)
    print("C. 其他电量/身份特性")
    print("=" * 74)
    for fid, name, fns in (
        (0x1000, "BATTERY_VOLTAGE", [(0x00, "getVoltage"), (0x10, "getCapabilities")]),
        (0x1001, "BATTERY_UNIT", [(0x00, "getBatteryUnit")]),
        (0x1814, "CHANGE_HOST", [(0x00, "getHostInfo")]),
        (0x0005, "DEVICE_TYPE_AND_NAME", [(0x00, "getDeviceType")]),
        (0x0006, "MODEL_ID", [(0x00, "getModelId")]),
    ):
        r = call(d, DEV, bytes([0x00, 0x00, (fid >> 8) & 0xFF, fid & 0xFF]).ljust(18, b"\x00"))
        if not r or len(r) < 5:
            print(f"  {name:<22}(0x{fid:04X}) 无响应"); continue
        fi = r[4]
        print(f"  {name:<22}(0x{fid:04X}) index=0x{fi:02X}")
        if fi == 0:
            continue
        for fn, fnm in fns:
            rr = h2(d, DEV, fi, fn)
            print(f"      fn{fn:02X} {fnm:<18} -> {rr[:14].hex(' ') if rr else 'None'}")
        time.sleep(0.15)

    d.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())