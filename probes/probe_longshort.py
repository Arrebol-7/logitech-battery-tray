"""验证关键假设：HID++ 2.0 请求必须走长报文(Report ID 0x11)才会被转发给设备。

对照实验，全部打到 dev=0x01：
    A. 短报文 0x10 发 ping      (预期 timeout)
    B. 长报文 0x11 发 ping      (预期 成功)
    A2/B2. 同样对比 1.0 寄存器 0x07 (已知短报文能秒回错误)
"""
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time

from probe_channels import Dev, list_vendor_paths, INVALID_HANDLE_VALUE

DEV = 0x01

def fmt(r):
    return r.hex(" ") if r else "(无)"

def call(d, dev, payload, report_id, size=None):
    if size is None:
        size = 7 if report_id == 0x10 else 20
    msg = bytes([report_id, dev]) + bytes(payload)
    msg = msg + bytes(size - len(msg))
    d.drain(60)
    wr = d.write(msg[:size], 400)
    if wr < 0:
        return None, f"write fail({wr})"
    deadline = time.time() + 1.0
    while time.time() < deadline:
        r = d.read(32, 200)
        if not r or r[0] not in (0x10, 0x11):
            continue
        if len(r) > 1 and r[1] != dev:
            continue
        return r, None
    return None, "timeout"

def main():
    paths = list_vendor_paths()
    paths.sort(key=lambda p: 0 if "col01" in p.lower() else 1)
    d = Dev(paths[0])
    if d.h == INVALID_HANDLE_VALUE:
        print("打开失败 err=", __import__("ctypes").get_last_error()); return 1

    print("=" * 74)
    print("对照实验  dev=0x01")
    print("=" * 74)

    # --- A: 短报文 2.0 ping ---
    r, e = call(d, DEV, bytes([0x00, 0x10, 0x55, 0x00, 0x00]), 0x10)
    print(f"A. 短0x10 ping                 -> {fmt(r)}  {e or ''}")

    # --- B: 长报文 2.0 ping ---
    r, e = call(d, DEV, bytes([0x00, 0x10, 0x55]) + bytes(15), 0x11)
    print(f"B. 长0x11 ping                 -> {fmt(r)}  {e or ''}")

    # --- C: 短报文 2.0 getFeature(0x1000) ---
    r, e = call(d, DEV, bytes([0x00, 0x00, 0x10, 0x00, 0x00]), 0x10)
    print(f"C. 短0x10 getFeature(0x1000)   -> {fmt(r)}  {e or ''}")

    # --- D: 长报文 2.0 getFeature(0x1000) ---
    r, e = call(d, DEV, bytes([0x00, 0x00, 0x10, 0x00]) + bytes(14), 0x11)
    print(f"D. 长0x11 getFeature(0x1000)   -> {fmt(r)}  {e or ''}")

    # --- E: 短报文 1.0 寄存器(已知能回) ---
    r, e = call(d, DEV, bytes([0x81, 0x07, 0x00, 0x00, 0x00]), 0x10)
    print(f"E. 短0x10 reg0x07 (1.0对照)    -> {fmt(r)}  {e or ''}")

    # --- F: 长报文 1.0 寄存器 ---
    r, e = call(d, DEV, bytes([0x81, 0x07]) + bytes(16), 0x11)
    print(f"F. 长0x11 reg0x07 (1.0对照)    -> {fmt(r)}  {e or ''}")

    # --- G: 长报文 2.0 getFeature 到 0xFF (接收器自身, 应回 0x08/0x01) ---
    r, e = call(d, 0xFF, bytes([0x00, 0x00, 0x10, 0x00]) + bytes(14), 0x11)
    print(f"G. 长0x11 getFeature -> 0xFF    -> {fmt(r)}  {e or ''}")

    # --- H: 长报文 1.0 reg0x02 到 0xFF (已知成功) ---
    r, e = call(d, 0xFF, bytes([0x81, 0x02]) + bytes(16), 0x11)
    print(f"H. 长0x11 reg0x02 -> 0xFF      -> {fmt(r)}  {e or ''}")

    # --- I: 直接用 col02 接口发长报文 2.0 ---
    if len(paths) > 1 and "col02" in paths[1].lower():
        pass
    d.close()

    # col02 单独试
    for p in paths:
        if "col02" not in p.lower():
            continue
        print("\n" + "=" * 74)
        print("col02 接口 (长报文专用?)")
        print("=" * 74)
        d2 = Dev(p)
        if d2.h == INVALID_HANDLE_VALUE:
            print("  col02 打开失败"); continue
        r, e = call(d2, DEV, bytes([0x00, 0x10, 0x55]) + bytes(15), 0x11)
        print(f"  长0x11 ping -> 0x01          -> {fmt(r)}  {e or ''}")
        r, e = call(d2, DEV, bytes([0x00, 0x00, 0x10, 0x00]) + bytes(14), 0x11)
        print(f"  长0x11 getFeature(0x1000)    -> {fmt(r)}  {e or ''}")
        r, e = call(d2, 0xFF, bytes([0x81, 0x02]) + bytes(16), 0x11)
        print(f"  长0x11 reg0x02 -> 0xFF       -> {fmt(r)}  {e or ''}")
        d2.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())