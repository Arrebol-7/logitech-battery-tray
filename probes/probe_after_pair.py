"""重新配对后重测 + 抓原始 Input Report 字节流。

关键新增：后台线程持续读 mi_00 和 mi_02，看动鼠标时是否有数据在流。
如果有数据流 -> 设备确实在线，问题在 HID++ 查询姿势
如果完全静默 -> 设备确实不在线
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import threading
import time

from probe_channels import list_vendor_paths, Dev
import probe_channels as pc

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
SHORT = 0x10

def scan_slots(dev):
    print("=" * 74)
    print("第 1 步：槽位扫描（重新配对后）")
    print("=" * 74)
    results = {}
    for di in [0xFF, 0x00] + list(range(1, 8)):
        got = None
        for _ in range(3):
            msg = bytes([SHORT, di, 0x02, 0x00]) + bytes(3)
            if dev.write(msg) >= 0:
                r = dev.read(7, 700)
                if r is not None:
                    got = r
                    break
            time.sleep(0.05)
        results[di] = got
        if got is None:
            print(f"  0x{di:02X}: 静默")
        else:
            print(f"  0x{di:02X}: {got.hex(' ')}")
    return results

def monitor_reports(path, seconds, label, size=20):
    """后台持续读取原始 Input Report。"""
    d = Dev(path)
    if d.h == INVALID_HANDLE_VALUE:
        return []
    samples = []
    try:
        d.drain(50)
        t0 = time.time()
        while time.time() - t0 < seconds:
            r = d.read(size, 150)
            if r:
                samples.append((round(time.time() - t0, 2), r.hex(" ")))
    finally:
        d.close()
    return samples

def main():
    paths = list_vendor_paths()
    col01 = [p for p in paths if "col01" in p.lower()]
    col02 = [p for p in paths if "col02" in p.lower()]
    if not col01:
        print("无 col01 接口"); return 1

    dev = Dev(col01[0])
    if dev.h == INVALID_HANDLE_VALUE:
        print("打开失败"); return 2

    try:
        res = scan_slots(dev)

        print()
        print("=" * 74)
        print("第 2 步：对每个有响应的槽位，试标准特性查询")
        print("=" * 74)
        for di in [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0xFF]:
            if res.get(di) is None:
                continue
            print(f"  --- dev 0x{di:02X} ---")
            for fi, fn, p, desc in ((0x00, 0x01, b"", "getFeatureCount"),
                                    (0x00, 0x00, bytes([0x10, 0x04]), "getFeature 0x1004"),
                                    (0x00, 0x00, bytes([0x10, 0x01]), "getFeature 0x1001"),
                                    (0x00, 0x00, bytes([0x10, 0x00]), "getFeature 0x1000"),
                                    (0x00, 0x00, bytes([0x00, 0x00]), "getFeature 0x0000")):
                msg = bytes([SHORT, di, fi, fn]) + p
                msg = msg + bytes(7 - len(msg))
                if dev.write(msg[:7]) < 0:
                    print(f"    {desc:22s} 写失败"); continue
                r = dev.read(7, 800)
                if r is None:
                    print(f"    {desc:22s} 超时")
                else:
                    code = r[4] if len(r) > 4 else -1
                    print(f"    {desc:22s} {r.hex(' ')}  (b4=0x{code:02X})")
    finally:
        dev.close()

    print()
    print("=" * 74)
    print("第 3 步：抓原始 Input Report（8 秒，请一直移动鼠标！）")
    print("=" * 74)
    for label, plist in (("vendor col01 (mi_02)", col01),
                         ("vendor col02 (mi_02)", col02)):
        if not plist:
            continue
        print(f"\n  --- {label} ---")
        samples = monitor_reports(plist[0], 8, label, 20)
        if samples:
            print(f"    收到 {len(samples)} 条:")
            for t, h in samples[:15]:
                print(f"      [{t:>5.2f}s] {h}")
        else:
            print("    8 秒内无任何数据 —— 完全静默")

    print()
    print("=" * 74)
    print("结论判据")
    print("=" * 74)
    return 0

if __name__ == "__main__":
    sys.exit(main())