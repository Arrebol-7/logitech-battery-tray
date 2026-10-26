"""设备在线性判定：鼠标在动的情况下，接收器为什么不转发。

要点：
  - 先垫一次占位请求唤醒链路，再发真正的探测
  - 多轮重试 + 更长超时
  - 区分 dev_index 0x01/0x02...0x06（接收器最多 6 个配对槽）
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time

from probe_channels import list_vendor_paths, Dev

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
SHORT = 0x10

# 1.0 vs 2.0 两种 root 调用都试
TESTS = [
    # (说明, feat_index, function, params)
    ("2.0 root.getFeatureCount", 0x00, 0x01, b""),
    ("2.0 root.getFeature(0x1004)", 0x00, 0x00, bytes([0x10, 0x04])),
    ("2.0 root.getFeature(0x1001)", 0x00, 0x00, bytes([0x10, 0x01])),
    ("2.0 root.getFeature(0x1000)", 0x00, 0x00, bytes([0x10, 0x00])),
    ("1.0 reg0x07 (name len)", 0x07, 0x00, b""),
    ("1.0 reg0x0D (battery?)", 0x0D, 0x00, b""),
    ("1.0 reg0x51 (battery)", 0x51, 0x00, b""),
]

def call(dev, dev_index, feat_index, function, params=b"", size=7, timeout=700, retries=3):
    msg = bytes([SHORT, dev_index, feat_index, function]) + params
    msg = msg + bytes(size - len(msg))
    for attempt in range(retries):
        wr = dev.write(msg[:size])
        if wr < 0:
            time.sleep(0.05)
            continue
        r = dev.read(size, timeout)
        if r is not None:
            return r, None
        time.sleep(0.1)
    return None, "timeout x%d" % retries

def parse(r):
    if r is None:
        return "无响应"
    if len(r) >= 5 and r[2] == 0x8F:
        code = r[4]
        hint = {
            0x00: "成功/保留", 0x01: "未知功能", 0x02: "无效参数",
            0x03: "越界", 0x04: "硬件错误", 0x05: "无效设备",
            0x06: "资源不足", 0x07: "内部错误", 0x08: "连接失败",
            0x09: "请求不可用", 0x0A: "参数过短", 0x0B: "参数过长",
        }.get(code, "未知")
        return f"接收器级错误 code=0x{code:02X} ({hint})"
    if len(r) >= 5 and r[3] == 0x8F:
        return f"功能级错误 code=0x{r[4]:02X}"
    return f"正常 {r.hex(' ')}"

def main():
    paths = list_vendor_paths()
    col01 = [p for p in paths if "col01" in p.lower()]
    if not col01:
        print("无 col01 接口")
        return 1
    dev = Dev(col01[0])
    if dev.h == INVALID_HANDLE_VALUE:
        print("打开失败 err=", ctypes.get_last_error())
        return 2

    print("链路唤醒：连发 5 次占位请求...")
    try:
        for _ in range(5):
            dev.write(bytes([SHORT, 0xFF, 0x00, 0x01, 0, 0, 0]))
            time.sleep(0.08)
        dev.drain(80)
    except Exception as e:
        print("  唤醒异常:", e)

    print("\n扫描全部配对槽位 (0xFF, 0x00, 0x01..0x06):")
    order = [0xFF, 0x00] + list(range(1, 7))
    any_live = []
    for di in order:
        # 先发一条，看有没有任何响应
        r, err = call(dev, di, 0x00, 0x01, b"", retries=2)
        tag = "有响应" if r is not None else "静默"
        if r is not None:
            any_live.append(di)
        print(f"  dev_index=0x{di:02X}: {tag:6s} {parse(r) if r is not None else ''}")

    if not any_live:
        print("\n所有槽位静默")
        dev.close()
        return 3

    print(f"\n有响应的槽位: {[hex(x) for x in any_live]}")
    for di in any_live:
        print(f"\n=== 详细探测 dev_index=0x{di:02X} ===")
        for name, fi, fn, p in TESTS:
            r, err = call(dev, di, fi, fn, p)
            print(f"  {name:32s} -> {parse(r) if r is not None else err}")
    dev.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())