"""接收器/设备状态诊断：确认鼠标是否真的连着接收器。

要点：
  - 区分"接收器回应"和"设备回应"
  - 修掉上一版把 0x8F 错误响应当成数据的解析 bug
  - 试 HID++ 1.0 (0x10 + 0x20 寄存功能) 判断配对状态
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from probe_channels import list_vendor_paths, Dev

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
SHORT = 0x10

def call(dev, dev_index, feat_index, function, params=b"", size=7, timeout=900):
    msg = bytes([SHORT, dev_index, feat_index, function]) + params
    msg = msg + bytes(size - len(msg))
    wr = dev.write(msg[:size])
    if wr < 0:
        return None, f"write fail({wr})"
    r = dev.read(size, timeout)
    if r is None:
        return None, "timeout"
    return r, None

def describe(r):
    """正确解析 HID++ 响应。"""
    if r is None:
        return "无响应"
    if len(r) < 5:
        return f"短响应 {r.hex(' ')}"
    # 错误响应：byte1=0xFF(接收器) 且 byte2=0x8F，或 byte3=0x8F
    if r[2] == 0x8F:
        return f"错误(接收器级) code=0x{r[4]:02X}"
    if r[3] == 0x8F:
        return f"错误(功能级) code=0x{r[4]:02X}"
    return f"正常 {r.hex(' ')}"

def main():
    paths = list_vendor_paths()
    col01 = [p for p in paths if "col01" in p.lower()]
    col02 = [p for p in paths if "col02" in p.lower()]

    for label, plist in (("col01", col01), ("col02", col02)):
        if not plist:
            continue
        print(f"\n{'='*70}\n### 接口 {label}")
        dev = Dev(plist[0])
        if dev.h == INVALID_HANDLE_VALUE:
            print("  打开失败"); continue
        try:
            print("\n-- ROOT(0x0000) 基础功能探测 --")
            tests = [
                ("getFeatureCount fn0x01", 0x00, 0x01, b""),
                ("getFeature(0x1001) fn0x00", 0x00, 0x00, bytes([0x10, 0x01])),
                ("getFeature(0x1004) fn0x00", 0x00, 0x00, bytes([0x10, 0x04])),
                ("getFeature(0x0000) fn0x00", 0x00, 0x00, bytes([0x00, 0x00])),
                ("getFeature(0x0001) fn0x00", 0x00, 0x00, bytes([0x00, 0x01])),
            ]
            for di in (0xFF, 0x00, 0x01):
                print(f"  dev_index=0x{di:02X}:")
                for name, fi, fn, p in tests:
                    r, err = call(dev, di, fi, fn, p)
                    if r is None:
                        print(f"    {name:32s} -> {err}")
                    else:
                        print(f"    {name:32s} -> {describe(r)}")

            # HID++ 1.0 寄存器：0x02 = connection state / pairing info
            print("\n-- HID++ 1.0 寄存器探测 (dev 0x01) --")
            for reg in (0x00, 0x02, 0x51, 0x81):
                r, err = call(dev, 0x01, reg, 0x00)
                if r is None:
                    print(f"    reg 0x{reg:02X} -> {err}")
                else:
                    print(f"    reg 0x{reg:02X} -> {describe(r)}")
        finally:
            dev.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())