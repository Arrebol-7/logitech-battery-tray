"""按 Solaar 权威格式的接收器寄存器探测（本仓定制，非通用库）。

协议事实（全部来自 Solaar 源码 _ref/base.py + _ref/hidpp10*.py）：
- HID++ 1.0 短报文: [0x10, dev, sub_id, address, p1, p2, p3]（7 字节）
- 读寄存器:  request_id = 0x8100 | reg  ->  [0x10, dev, 0x81, reg, p1, p2, p3]
- 写寄存器:  request_id = 0x8000 | reg  ->  [0x10, dev, 0x80, reg, v1, v2, v3]
- 长寄存器(0x2XX): request_id = 0x8100 | 0x2FF 区间 -> 走 20 字节长报文 [0x11, dev, ...]
- 错误响应:  [0x10, dev, 0x8F, 原sub_id, 原address, 错误码, 0x00]
  错误码: 0x01 INVALID_SUB_ID_COMMAND / 0x08 UNKNOWN_DEVICE / ...
- ping: request_id = 0x0010|sw_id, 参数 (0,0,随机mark)；响应 [0x10,dev,0x00,0x10,主,次,mark]
  响应数据帧 data[0:2] 必须等于请求的 request_id（0x00 0x10|sw_id）。
- 0x8F 错误帧的 data[1:3] 必须回显请求前两字节。

我此前的三个误读（已翻案）：
1. 把寄存器号当命令码发（10 FF <reg> ...）-> 回 0x01 是"子命令无效"，链路其实通。
2. probe_reg10.py 发 81 <reg_hi> <reg_lo> 把寄存器号拆两字节 -> 实际读的是 reg<<8。
   "10 ff 81 f1 01 04 02" 实为读 0xF1 固件寄存器(参数0x01) -> 数据 04 02 = 固件 4.02，
   与 USB REV_0402 吻合 —— 接收器一直在真实应答。
3. "0x11 write fail" 是 7 字节缓冲写 20 字节集合 -> 缓冲长度错，不是通道不通。
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time

from probe_channels import list_vendor_paths, Dev

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

SHORT_ID = 0x10
LONG_ID = 0x11
SHORT_LEN = 7
LONG_LEN = 20
RECV = 0xFF

# Solaar ErrorCode（hidpp10_constants.py）
ERR_NAMES = {
    0x01: "INVALID_SUB_ID_COMMAND 子命令无效",
    0x02: "INVALID_ADDRESS 地址无效",
    0x03: "INVALID_VALUE 值无效",
    0x04: "CONNECTION_REQUEST_FAILED 连接请求失败",
    0x05: "TOO_MANY_DEVICES 设备过多",
    0x06: "ALREADY_EXISTS 已存在",
    0x07: "BUSY 忙",
    0x08: "UNKNOWN_DEVICE 设备未知",
    0x09: "RESOURCE_ERROR 资源错误",
    0x0A: "REQUEST_UNAVAILABLE 请求不可用",
    0x0B: "UNSUPPORTED_PARAMETER_VALUE 参数不支持",
    0x0C: "WRONG_PIN_CODE PIN 错误",
}

_sw_id = [0]

def next_sw_id():
    _sw_id[0] = (_sw_id[0] + 1) % 8
    return _sw_id[0]

def drain(dev, max_rounds=10):
    n = 0
    for _ in range(max_rounds):
        r = dev.read(LONG_LEN, 60)
        if r is None:
            break
        n += 1
    return n

def send_short(dev, dev_index, sub_id, address, p1=0, p2=0, p3=0):
    msg = bytes([SHORT_ID, dev_index, sub_id, address, p1, p2, p3])
    if dev.write(msg) < 0:
        return False
    return True

def send_long(dev, dev_index, sub_id, address, p1=0, p2=0, p3=0):
    msg = bytes([LONG_ID, dev_index, sub_id, address, p1, p2, p3]) + bytes(LONG_LEN - SHORT_LEN)
    if dev.write(msg) < 0:
        return False
    return True

def collect(dev, timeout_s=1.2, max_frames=6):
    """收集响应帧。返回 [(frame_bytes, ...)]，每帧去掉 Report ID 后为 6 字节。"""
    frames = []
    deadline = time.time() + timeout_s
    while time.time() < deadline and len(frames) < max_frames:
        r = dev.read(SHORT_LEN, 250)
        if r is None:
            continue
        frames.append(bytes(r))
    return frames

def classify(frames, req_short, req_data2):
    """req_short: 请求的 [sub_id, address]；req_data2: 请求前两字节 bytes。

    响应帧布局（去掉 Report ID）：[dev, sub, addr, d0, d1, d2]
    数据帧判定：dev==0xFF 且 sub==req_sub 且 addr==req_addr。
    错误帧判定：sub==0x8F 且 addr==req_sub 且 d0==req_addr。
    """
    data_frames, error_frames, other = [], [], []
    for f in frames:
        if len(f) < 7:
            other.append((f, "短帧"))
            continue
        rdev, sub, addr = f[1], f[2], f[3]
        if sub == 0x8F and addr == req_short[0]:
            code = f[5]
            error_frames.append((f, f"错误 0x{code:02X} {ERR_NAMES.get(code, '?')}"))
        elif rdev == 0xFF and sub == req_short[0] and addr == req_short[1]:
            data_frames.append((f, "数据帧"))
        else:
            other.append((f, f"其它 dev=0x{rdev:02X} sub=0x{sub:02X}"))
    return data_frames, error_frames, other

def request(dev, dev_index, sub_id, address, params=(0, 0, 0), use_long=False,
            timeout_s=1.2):
    """发一条请求并按 Solaar 语义解析。返回 ('data'|'error'|'timeout'|'other', 详情)"""
    drain(dev)
    if use_long:
        ok = send_long(dev, dev_index, sub_id, address, *params)
    else:
        ok = send_short(dev, dev_index, sub_id, address, *params)
    if not ok:
        return ("write-fail", None)
    frames = collect(dev, timeout_s)
    d, e, o = classify(frames, (sub_id, address), None)
    if d:
        return ("data", d[0][0])
    if e:
        return ("error", e[0][1])
    if o:
        return ("other", o[0][0].hex(" "))
    return ("timeout", None)

def ping(dev, dev_index, timeout_s=1.5):
    """Solaar ping: request_id=0x0010|sw_id, 参数 (0,0,mark)。响应 data[0:2]==请求id。"""
    drain(dev)
    sw = next_sw_id()
    req_id = 0x0010 | sw
    mark = (dev_index * 13 + 0x5A) & 0xFF
    send_short(dev, dev_index, req_id >> 8, req_id & 0xFF, 0x00, 0x00, mark)
    frames = collect(dev, timeout_s)
    for f in frames:
        if len(f) < 7:
            continue
        rdev, sub, addr = f[1], f[2], f[3]
        if rdev == dev_index and sub == (req_id >> 8) and addr == (req_id & 0xFF):
            major, minor, rmark = f[4], f[5], f[6]
            if rmark == mark:
                return ("online", f"HID++ {major}.{minor}")
            return ("online-nomark", f"主={major} 次={minor} mark不符")
        if rdev == dev_index and sub == 0x8F:
            code = f[5]
            if code == 0x01:
                return ("hidpp10", "HID++ 1.0 设备（0x01=子命令无效但设备在）")
            if code in (0x08, 0x09):
                return ("offline", f"错误 0x{code:02X} = 设备不可达")
            return ("error", f"错误 0x{code:02X} {ERR_NAMES.get(code, '?')}")
    return ("timeout", None)

def main():
    paths = list_vendor_paths()
    col01 = [p for p in paths if "col01" in p.lower()]
    if not col01:
        print("无 col01 接口"); return 1
    dev = Dev(col01[0])
    if dev.h == INVALID_HANDLE_VALUE:
        print("打开失败"); return 2

    try:
        print("=" * 78)
        print("Solaar 权威格式探测  [0x10, dev, 0x81, reg, p1, p2, p3]")
        print("=" * 78)

        # ---- 第 1 组：接收器自身短寄存器 (dev=0xFF) ----
        print("\n[A] 接收器短寄存器 (dev=0xFF)")
        receiver_regs = [
            (0x00, "Notifications 通知标志"),
            (0x02, "Receiver Connection 连接状态"),
            (0xF1, "Firmware 固件(参数0x01)"),
        ]
        for reg, name in receiver_regs:
            status, detail = request(dev, RECV, 0x81, reg)
            extra = ""
            if status == "data" and detail is not None:
                extra = "  原始: " + detail.hex(" ")
                if reg == 0x02:
                    print(f"  0x{reg:02X} {name:34s} -> {status}")
                    print(f"      {extra}")
                    # Solaar: 读 0x02 响应 data[0]=已连接设备位图
                    print(f"      data[0]=0x{detail[3]:02X} bit0={'有设备' if detail[3] & 1 else '无设备'}")
                    continue
                if reg == 0xF1:
                    ver = detail[4:6].hex()
                    extra += f"  -> 固件版本 {ver[0:2]}.{ver[2:4]}"
            print(f"  0x{reg:02X} {name:34s} -> {status} {extra}")

        # ---- 第 2 组：接收器长寄存器 (0x2B5 接收器信息, 20字节长报文) ----
        print("\n[B] 接收器长寄存器 0x2B5 (20字节长报文)")
        for sub, p2, label in ((0x01, 0, "序列号"), (0x02, 0, "固件版本"),
                               (0x03, 0, "接收器信息"), (0x20, 1, "配对信息 dev1"),
                               (0x30, 1, "扩展配对信息 dev1")):
            status, detail = request(dev, RECV, 0x81, 0xB5, params=(sub, p2, 0), use_long=True)
            extra = detail.hex(" ") if isinstance(detail, bytes) else (detail or "")
            print(f"  0x2B5 sub=0x{sub:02X} {label:20s} -> {status}  {extra}")

        # ---- 第 3 组：ping 全槽位 ----
        print("\n[C] ping 全槽位 (判断哪个槽位有活设备)")
        online_slots = []
        for di in range(0x00, 0x08):
            status, detail = ping(dev, di)
            flag = ""
            if status in ("online", "hidpp10"):
                online_slots.append(di)
                flag = "  <<< 有设备!"
            print(f"  dev 0x{di:02X}: {status:14s} {detail or ''}{flag}")

        # ---- 第 4 组：对在线设备读电池 ----
        if online_slots:
            print("\n[D] 对在线槽位读电池寄存器")
            for di in online_slots:
                for reg, label, parser in (
                    (0x0D, "BATTERY_CHARGE 电量", "charge"),
                    (0x07, "BATTERY_STATUS 状态", "status"),
                ):
                    status, detail = request(dev, di, 0x81, reg)
                    if status == "data" and isinstance(detail, bytes):
                        raw = detail[3:7]
                        print(f"  dev 0x{di:02X} reg 0x{reg:02X} {label}: {raw.hex(' ')}")
                        if parser == "charge":
                            pct, state2 = raw[0], raw[1]
                            print(f"      >>> 电量 = {pct}%  状态字节=0x{state2:02X}")
                        else:
                            lvl, chg = raw[1], raw[2]
                            print(f"      >>> 状态=0x{chg:02X} (0=放电 1=充电 2=充满)")
                    else:
                        print(f"  dev 0x{di:02X} reg 0x{reg:02X} {label}: {status}")
        else:
            print("\n[D] 无在线槽位，跳过电池读取")

        # ---- 第 5 组：长报文对 0x11 的写测（确认长报文通道真实状态）----
        print("\n[E] 长报文 0x11 通道直测 (ping dev=1, 20字节)")
        status, detail = ping(dev, 0x01)
        print(f"  短报文 ping dev1: {status} {detail or ''}")
        drain(dev)
        ok = send_long(dev, 0x01, 0x81, 0x0D)
        print(f"  长报文写 dev1 读0x0D: {'写入成功' if ok else '写入失败'}")
        frames = collect(dev, 1.2)
        for f in frames:
            print(f"    响应: {f.hex(' ')}")
        if not frames:
            print("    静默")
    finally:
        dev.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
