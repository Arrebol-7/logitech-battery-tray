"""精细化寄存器读取：先使能通知，再多次读，严格区分回显/数据/错误。

关键改进：
1. 每条命令前清空缓冲（读到 None 为止），避免读陈旧帧
2. 发命令后连续读 5 次（每次 500ms），记录所有帧
3. 严格判断：帧含"我发的寄存器号"=回显；帧含 0x8F=错误；其他=数据
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
RECV = 0xFF
GET_REG = 0x81
SET_REG = 0x80

def drain(dev, rounds=6):
    """清空输入缓冲区，返回被丢弃的帧数。"""
    n = 0
    for _ in range(rounds):
        r = dev.read(20, 80)
        if r is None:
            break
        n += 1
    return n

def probe(dev, reg, reads=5):
    drain(dev)
    msg = bytes([SHORT, RECV, GET_REG, (reg >> 8) & 0xFF, reg & 0xFF, 0x00, 0x00])
    if dev.write(msg) < 0:
        return "写失败", []
    frames = []
    for _ in range(reads):
        r = dev.read(20, 500)
        if r:
            frames.append(r)
    return None, frames

def classify(frames, reg):
    """判断帧类型。"""
    out = []
    for f in frames:
        if len(f) < 3:
            out.append(("碎片", f))
        elif f[2] == 0x8F:
            code = f[4] if len(f) > 4 else -1
            names = {0x00: "成功", 0x01: "未知功能", 0x02: "无效参数", 0x03: "越界",
                     0x04: "硬件错误", 0x05: "无效设备", 0x06: "资源不足",
                     0x07: "内部错误", 0x08: "连接失败", 0x09: "请求不可用"}
            out.append((f"错误/0x{code:02X}({names.get(code,'?')})", f))
        elif f[2] == GET_REG and len(f) >= 5 and f[3] == (reg >> 8) & 0xFF and f[4] == reg & 0xFF:
            out.append(("回显", f))
        else:
            out.append(("**数据**", f))
    return out

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
        print("先使能通知标志 (reg 0x0000 <- 0x000100)")
        print("=" * 78)
        drain(dev)
        m = bytes([SHORT, RECV, SET_REG, 0x00, 0x00, 0x01, 0x00])
        print(f"  发送: {m.hex(' ')}")
        if dev.write(m) < 0:
            print("  写失败")
        else:
            time.sleep(0.3)
            for _ in range(4):
                r = dev.read(20, 400)
                if r:
                    print(f"  回应: {r.hex(' ')}")

        print()
        print("=" * 78)
        print("逐寄存器精细读取（发后连读5帧）")
        print("=" * 78)
        regs = [
            (0x0000, "Notifications 通知标志"),
            (0x0002, "Connection State 连接状态"),
            (0x0051, "Receiver Info 接收器信息"),
            (0x00B5, "Pairing Info 配对信息"),
            (0x00F1, "Firmware 固件"),
            (0x01F1, "Firmware long 固件长版"),
        ]
        for reg, name in regs:
            err, frames = probe(dev, reg)
            print(f"\n  0x{reg:04X} {name}  ({len(frames)} 帧)")
            if err:
                print(f"    {err}")
            for tag, f in classify(frames, reg):
                print(f"    [{tag}] {f.hex(' ')}")

        print()
        print("=" * 78)
        print("长报文测试 (Report ID 0x11, 20字节)  — 0x10 通道设通知后是否切到 0x11")
        print("=" * 78)
        # 直接对 0x11 报告 ID 发同样的读命令
        drain(dev)
        msg11 = bytes([0x11, RECV, GET_REG, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00,
                       0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        print(f"  发送 20B: {msg11.hex(' ')}")
        if dev.write(msg11) < 0:
            print("  写失败（0x11 通道不可用）")
        else:
            for _ in range(3):
                r = dev.read(20, 500)
                if r:
                    print(f"  回应: {r.hex(' ')}")
    finally:
        dev.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())