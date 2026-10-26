"""按 HID++ 1.0 正确的寄存器格式重测。

正确格式（来自 Solaar 实测）：
  读寄存器: 10 FF 81 <reg_hi> <reg_lo> 00 00
  写寄存器: 10 FF 80 <reg_hi> <reg_lo> <val_hi> <val_lo>

我之前的错误：把寄存器号当成了命令码（10 FF <reg> ...）。
正确做法：命令码固定 0x81/0x80，寄存器号放参数位。

关键寄存器：
  0x0000 通知标志      0x0002 连接状态(响应[1]=设备数)
  0xB500 配对信息      0x0051 接收器信息(响应[6]=max_devices)
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
SET_REGISTER = 0x80

def read_register(dev, reg, size=7, timeout=1000, retries=3):
    """10 FF 81 <reg_hi> <reg_lo> 00 00"""
    msg = bytes([SHORT, RECEIVER_INDEX, GET_REGISTER,
                 (reg >> 8) & 0xFF, reg & 0xFF, 0x00, 0x00])
    for _ in range(retries):
        if dev.write(msg) < 0:
            time.sleep(0.05)
            continue
        r = dev.read(size, timeout)
        if r is not None:
            return r
        time.sleep(0.1)
    return None

def parse(r):
    if r is None:
        return "无响应"
    if len(r) >= 4 and r[2] == 0x8F:
        code = r[4] if len(r) > 4 else -1
        hints = {0x00: "成功", 0x01: "未知功能", 0x02: "无效参数",
                 0x03: "越界", 0x04: "硬件错误", 0x05: "无效设备",
                 0x06: "资源不足", 0x07: "内部错误", 0x08: "连接失败",
                 0x09: "请求不可用"}
        return f"错误 0x{code:02X} ({hints.get(code, '?')})"
    return f"OK  {r.hex(' ')}"

def main():
    paths = list_vendor_paths()
    col01 = [p for p in paths if "col01" in p.lower()]
    if not col01:
        print("无 col01 接口"); return 1
    dev = Dev(col01[0])
    if dev.h == INVALID_HANDLE_VALUE:
        print("打开失败"); return 2

    try:
        print("=" * 76)
        print("HID++ 1.0 寄存器读取（正确格式: 10 FF 81 reg_hi reg_lo 00 00）")
        print("=" * 76)

        regs = [
            (0x0000, "通知标志 Notifications"),
            (0x0002, "连接状态 Connection State  [响应[1]=设备数]"),
            (0x0051, "接收器信息 Receiver Info  [响应[6]=max_devices]"),
            (0x00B3, "设备活动 Device Activity"),
            (0xB500, "配对信息 sub0  [响应[3:5]=wpid]"),
            (0xB501, "配对信息 sub1"),
            (0xB502, "配对信息 sub2"),
            (0xB503, "配对信息 sub3"),
            (0xB510, "配对信息 sub0x10"),
            (0xB520, "配对信息 sub0x20"),
            (0xB530, "配对信息 sub0x30"),
            (0xF100, "固件版本 Firmware 0"),
            (0xF101, "固件版本 Firmware 1"),
        ]

        results = {}
        for reg, name in regs:
            r = read_register(dev, reg)
            results[reg] = r
            print(f"  0x{reg:04X}  {name:42s} -> {parse(r)}")

        print()
        print("=" * 76)
        print("解读")
        print("=" * 76)

        conn = results.get(0x0002)
        if conn:
            print(f"  连接状态原始: {conn.hex(' ')}")
            print(f"    响应[1] = 0x{conn[1]:02X} = {conn[1]}  <- 已连接设备数量")
            if conn[1] == 0:
                print("    !! 接收器报告 0 个设备连接 !!")
            else:
                print(f"    -> 有 {conn[1]} 个设备连着，槽位 1..{conn[1]}")
                for n in range(1, conn[1] + 1):
                    print(f"       设备 {n} 应可用 dev_index=0x{n:02X}")

        info = results.get(0x0051)
        if info:
            print(f"  接收器信息: {info.hex(' ')}")
            if len(info) > 6:
                print(f"    响应[6] = {info[6]} = max_devices")

        print()
        print("  --- 配对信息明细 (0xB5) ---")
        for sub in (0x00, 0x01, 0x02, 0x03, 0x10, 0x20, 0x30):
            r = results.get(0xB500 + sub)
            if r and r[2] != 0x8F and len(r) >= 5:
                wpid = r[3:5].hex().upper()
                print(f"    sub 0x{sub:02X}: wpid={wpid}  {r.hex(' ')}")
            elif r:
                print(f"    sub 0x{sub:02X}: {parse(r)}")

        # 如果连接状态说有设备，立刻对它读电池
        print()
        print("=" * 76)
        print("如果检测到设备，尝试读其电池寄存器")
        print("=" * 76)
        if conn and conn[1] > 0:
            for n in range(1, min(conn[1], 6) + 1):
                di = n
                # HID++ 1.0 设备寄存器: 0x0D = 电池状态 (部分设备)
                for reg, label in ((0x0D, "电池状态 Battery Status"),
                                   (0x0F, "电池电量/充电"),
                                   (0x07, "设备名称长度")):
                    msg = bytes([SHORT, di, GET_REGISTER,
                                 (reg >> 8) & 0xFF, reg & 0xFF, 0x00, 0x00])
                    if dev.write(msg) < 0:
                        print(f"  dev 0x{di:02X} reg 0x{reg:02X} ({label}): 写失败")
                        continue
                    r = dev.read(7, 1000)
                    print(f"  dev 0x{di:02X} reg 0x{reg:02X} ({label}): {parse(r)}")
                    if r and reg == 0x0D and r[2] != 0x8F and len(r) >= 7:
                        # 电池状态: 响应[4]=电量百分比, 响应[5]=充电状态/电压
                        pct = r[4]
                        print(f"      >>> 电量字节 = 0x{pct:02X} = {pct} "
                              f"{'%' if pct <= 100 else '(可能是电压)'}")
        else:
            print("  接收器报告无设备连接，跳过")
    finally:
        dev.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())