"""修正版：双读取第二帧 + 正确解析偏移。

关键修正：
1. r[1] 是设备索引(0xFF)，不是数据。数据从 r[3] 开始。
2. HID++ 1.0 寄存器读取可能是"先回显再回数据"两段式 —— 读两次取第二帧。
3. 比较"回显"与"数据"的差别，找出真正有内容的寄存器。
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
RECEIVER_INDEX = 0xFF
GET_REGISTER = 0x81

# 接收器寄存器（HID++ 1.0，dev_index=0xFF）
RECEIVER_REGS = [
    (0x0000, "Notifications 通知标志"),
    (0x0002, "Connection State 连接状态"),
    (0x0003, "Device Activity 设备活动"),
    (0x0002, "Connection State (重读)"),
    (0x0051, "Receiver Info 接收器信息"),
    (0x00B5, "Pairing Info 配对信息"),
    (0x00F1, "Firmware 固件"),
]

def send_recv(dev, msg, size=7, timeout=1200):
    if dev.write(msg) < 0:
        return None
    return dev.read(size, timeout)

def read_register_twice(dev, reg, size=7):
    """发一次读命令，取两帧（第一帧可能是回显，第二帧是数据）。"""
    msg = bytes([SHORT, RECEIVER_INDEX, GET_REGISTER,
                 (reg >> 8) & 0xFF, reg & 0xFF, 0x00, 0x00])
    if dev.write(msg) < 0:
        return None, None
    f1 = dev.read(size, 800)
    f2 = dev.read(size, 800)
    return f1, f2

def hx(b):
    return b.hex(" ") if b else "None"

def main():
    paths = list_vendor_paths()
    col01 = [p for p in paths if "col01" in p.lower()]
    if not col01:
        print("无 col01"); return 1
    dev = Dev(col01[0])
    if dev.h == INVALID_HANDLE_VALUE:
        print("打开失败"); return 2
    try:
        print("=" * 78)
        print("HID++ 1.0 双读实验：区分「回显帧」与「数据帧」")
        print("=" * 78)
        for reg, name in RECEIVER_REGS:
            f1, f2 = read_register_twice(dev, reg)
            print(f"\n  寄存器 0x{reg:04X}  {name}")
            print(f"    第1帧: {hx(f1)}")
            print(f"    第2帧: {hx(f2)}")
            # 判断哪帧是数据：非回显帧的特征是 r[2] != 0x81 或长度>7
            for tag, f in (("1", f1), ("2", f2)):
                if f and len(f) >= 4:
                    if f[2] == 0x8F:
                        code = f[4] if len(f) > 4 else -1
                        print(f"    帧{tag} 是错误响应，code=0x{code:02X}")
                    elif f[2] == GET_REGISTER and f[3] == (reg >> 8) & 0xFF and f[4] == reg & 0xFF:
                        print(f"    帧{tag} 是回显（命令+寄存器号一致，无数据）")
                    else:
                        print(f"    帧{tag} 是【数据帧】！字节: {[hex(x) for x in f]}")

        # 用长时间等待的纯监听，看是否有自发通知
        print()
        print("=" * 78)
        print("纯监听 3 秒（不动鼠标 vs 动鼠标都试）")
        print("=" * 78)
        for i in range(2):
            print(f"  第 {i+1} 轮监听 3 秒...")
            got = False
            deadline = time.time() + 3.0
            while time.time() < deadline:
                r = dev.read(7, 300)
                if r:
                    print(f"    收到: {hx(r)}")
                    got = True
            if not got:
                print("    静默")
    finally:
        dev.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())