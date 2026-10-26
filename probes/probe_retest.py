"""重测 + 用接收器自身查询配对信息（HID++ 1.0），问出真实设备索引。

HID++ 1.0 接收器查询（register 0x02 = connection state）:
  发: 10 <slot> 02 00 00 00 00
  收: ... 第4字节起 = 是否能连上 + 设备类型
另外 0x81/0x80 用于取设备信息。
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

# 标准接收器信息寄存器
REG_CONNECTION = 0x02     # 连接状态 / 配对信息
REG_PAIRING = 0x0B        # 配对信息
REG_DEVICE_NAME_LEN = 0x07
REG_DEVICE_NAME = 0x08
REG_EXTENDED = 0x81       # 扩展寄存器入口

def raw_call(dev, dev_index, reg, params=b"", size=7, timeout=800):
    msg = bytes([SHORT, dev_index, reg]) + params
    msg = msg + bytes(size - len(msg))
    wr = dev.write(msg[:size])
    if wr < 0:
        return None
    return dev.read(size, timeout)

def main():
    paths = list_vendor_paths()
    col01 = [p for p in paths if "col01" in p.lower()]
    if not col01:
        print("无 col01 接口")
        return 1
    dev = Dev(col01[0])
    if dev.h == INVALID_HANDLE_VALUE:
        print("打开失败")
        return 2

    try:
        print("=" * 72)
        print("第 1 步：扫描所有槽位，看谁有响应")
        print("=" * 72)
        slots = [0xFF, 0x00] + list(range(1, 7))
        live = {}
        for di in slots:
            # 重试 3 次，避免偶发丢包
            got = None
            for _ in range(3):
                r = raw_call(dev, di, REG_CONNECTION, b"", timeout=600)
                if r is not None:
                    got = r
                    break
                time.sleep(0.05)
            live[di] = got
            if got is None:
                print(f"  0x{di:02X}: 静默")
            else:
                code = got[4] if len(got) > 4 else -1
                print(f"  0x{di:02X}: {got.hex(' ')}  (err_code=0x{code:02X})")

        # 找出行为与众不同的槽位（真正可能挂着设备的那个）
        print()
        print("=" * 72)
        print("第 2 步：区分'真设备槽'与'接收器统一拒绝'")
        print("=" * 72)
        distinct = {}
        for di, r in live.items():
            if r is None:
                continue
            key = bytes(r[2:])  # 忽略回显的 dev_index
            distinct.setdefault(key, []).append(di)
        if len(distinct) == 1:
            print("  所有响应槽位返回完全相同的内容 -> 接收器对任何 dev_index 都回同一错误")
            print("  说明：接收器没在区分设备，这些不是真实设备响应")
        elif live.get(0x01) is None and len(distinct) >= 1:
            print("  0x01 静默，其余槽位返回相同内容")
            print("  -> 接收器对 0x01 会尝试转发（转发无应答故静默），")
            print("     对其它索引直接拒绝。0x01 是最可能的设备槽位。")

        print()
        print("=" * 72)
        print("第 3 步：对 0x01 做多次重试（设备可能休眠，需要唤醒）")
        print("=" * 72)
        for attempt in range(6):
            r = raw_call(dev, 0x01, REG_CONNECTION, b"", timeout=1200)
            if r is not None:
                print(f"  第 {attempt+1} 次: 收到 {r.hex(' ')}")
                break
            print(f"  第 {attempt+1} 次: 超时")
            time.sleep(0.25)
        else:
            print("  6 次重试全部超时 —— 该槽位无设备应答")

        print()
        print("=" * 72)
        print("第 4 步：接收器自身信息（dev 0xFF）")
        print("=" * 72)
        for reg, name in ((0x00, "接收器协议版本"),
                          (0x02, "连接状态"),
                          (0xB0, "?"),
                          (0x51, "?")):
            r = raw_call(dev, 0xFF, reg, b"", timeout=800)
            print(f"  reg 0x{reg:02X} ({name}): {r.hex(' ') if r else '静默'}")

        print()
        print("=" * 72)
        print("第 5 步：10 秒持续监听（不发送），看设备是否主动推电量通知")
        print("=" * 72)
        dev.drain(50)
        cnt = 0
        t0 = time.time()
        while time.time() - t0 < 10:
            r = dev.read(32, 200)
            if r:
                print(f"  {r.hex(' ')}")
                cnt += 1
        print(f"  10 秒内收到 {cnt} 条主动上报")
    finally:
        dev.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())