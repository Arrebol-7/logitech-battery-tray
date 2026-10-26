"""按 Solaar 权威协议复刻的接收器探测（HID++ 1.0 寄存器）。

协议要点（来自 _ref/base.py + _ref/hidpp10.py + _ref/hidpp10_constants.py）：

    短报文 7B : [0x10, dev, sub_id, addr, p1, p2, p3]
    长报文 20B: [0x11, dev, sub_id, addr, p1, p2, p3, ...]
    读寄存器  : request_id = 0x8100 | (reg & 0x2FF)   -> 拆成 sub_id, addr 两字节
    写寄存器  : request_id = 0x8000 | (reg & 0x2FF)
    接收器自身: dev = 0xFF
    设备槽位  : dev = 0x01 .. 0x06

关键寄存器:
    0x00  NOTIFICATIONS        (读: 通知标志)
    0x02  RECEIVER_CONNECTION  (读: reply[1] = 已连接设备数)
    0xF1  FIRMWARE             (读: 参数 0x01 -> reply[1:3] = 版本)
    0x2B5 RECEIVER_INFO        (长寄存器, 子命令 0x03=信息, 0x20+n-1=配对信息, 0x30+n-1=扩展)
    0x07  BATTERY_STATUS       (设备侧, 1.0 电量)
    0x0D  BATTERY_CHARGE       (设备侧, 1.0 电量+充电状态)

错误码 (ErrorCode):
    0x01 INVALID_SUB_ID_COMMAND   子命令无效
    0x02 INVALID_ADDRESS
    0x08 UNKNOWN_DEVICE           设备未知(槽位没设备)
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time

from probe_channels import Dev, list_vendor_paths, INVALID_HANDLE_VALUE

RECEIVER = 0xFF
SHORT, LONG = 7, 20

def req(reg, *params, write=False):
    """按 Solaar 规则构造 request_id 的两字节。"""
    rid = (0x8000 if write else 0x8100) | (int(reg) & 0x2FF)
    return bytes([(rid >> 8) & 0xFF, rid & 0xFF]) + bytes(params)

def tx(d, dev, payload):
    """发一条请求并读响应。payload = [sub_id, addr, p1, p2, p3] (<=5B)。"""
    long_msg = len(payload) > 5 or payload[:1] == b"\x82"
    size = LONG if long_msg else SHORT
    report_id = 0x11 if long_msg else 0x10
    msg = bytes([report_id, dev]) + bytes(payload)
    msg = msg + bytes(size - len(msg))
    d.drain(60)
    wr = d.write(msg[:size], 400)
    if wr < 0:
        return None, f"write fail({wr})"
    # 精确匹配：report_id + dev 都要对上才算响应
    deadline = time.time() + 0.8
    while time.time() < deadline:
        r = d.read(size if size == SHORT else 32, 200)
        if not r:
            continue
        if r[0] not in (0x10, 0x11):
            continue
        if len(r) > 1 and r[1] != dev:
            continue          # 不是发给这个设备的，丢弃
        return r, None
    return None, "timeout"

def fmt(r):
    return r.hex(" ") if r else "(无)"

def decode_err(r):
    """如果 r[2] == 0x8F 说明是错误响应，r[5] 是错误码。"""
    if r and len(r) > 5 and r[2] == 0x8F:
        names = {0x01: "子命令无效", 0x02: "地址无效", 0x03: "值无效",
                 0x04: "连接请求失败", 0x05: "设备过多", 0x06: "已存在",
                 0x07: "忙", 0x08: "设备未知", 0x09: "资源错误",
                 0x0A: "请求不可用", 0x0B: "不支持参数", 0x0C: "PIN码错误"}
        return f"错误响应: {names.get(r[5], hex(r[5]))}"
    return None

def main():
    paths = list_vendor_paths()
    if not paths:
        print("没找到 mi_02 vendor 接口"); return 1
    # 优先 col01
    paths.sort(key=lambda p: 0 if "col01" in p.lower() else 1)
    path = paths[0]
    print(f"接口: ...{path.split('#')[-1][:40]}")
    d = Dev(path)
    if d.h == INVALID_HANDLE_VALUE:
        print("打开失败 err=", ctypes.get_last_error()); return 1

    print("\n" + "=" * 72)
    print("A. 接收器自身 (dev=0xFF)")
    print("=" * 72)
    tests = [
        ("通知标志 reg0x00", req(0x00)),
        ("连接状态 reg0x02", req(0x02)),
        ("固件 reg0xF1 p=01", req(0xF1, 0x01)),
        ("固件 reg0xF1 p=02", req(0xF1, 0x02)),
        ("固件 reg0xF1 p=03", req(0xF1, 0x03)),
        ("固件 reg0xF1 p=04", req(0xF1, 0x04)),
        ("接收器信息 reg0x2B5 sub=03", req(0x2B5, 0x03)),
        ("接收器信息 reg0x2B5 sub=01", req(0x2B5, 0x01)),
        ("接收器信息 reg0x2B5 sub=02", req(0x2B5, 0x02)),
    ]
    for name, payload in tests:
        r, err = tx(d, RECEIVER, payload)
        e = decode_err(r)
        print(f"  {name:<28} -> {fmt(r)}   {err or ''}{' [' + e + ']' if e else ''}")

    print("\n" + "=" * 72)
    print("B. 配对信息 reg0x2B5 sub=0x20+n-1 (n=1..6)  [看哪些槽位有设备]")
    print("=" * 72)
    for n in range(1, 7):
        r, err = tx(d, RECEIVER, req(0x2B5, 0x20 + n - 1))
        e = decode_err(r)
        extra = ""
        if r and not e and len(r) >= 8:
            wpid = (r[4] << 8) | r[3]
            kind = r[7] & 0x0F
            kinds = {0x01: "键盘", 0x02: "鼠标", 0x03: "小键盘", 0x08: "轨迹球",
                     0x09: "触摸板", 0x0F: "接收器"}
            extra = f"  WPID=0x{wpid:04X} 类型={kinds.get(kind, hex(kind))}"
        print(f"  槽位{n} -> {fmt(r)}   {err or ''}{extra}{' [' + e + ']' if e else ''}")

    print("\n" + "=" * 72)
    print("C. 设备侧电量 (dev=0x01..0x06, reg 0x07 / 0x0D)")
    print("=" * 72)
    for n in range(1, 7):
        for reg, label in ((0x07, "BATTERY_STATUS"), (0x0D, "BATTERY_CHARGE")):
            r, err = tx(d, n, req(reg))
            e = decode_err(r)
            extra = ""
            if r and not e and len(r) > 4:
                lvl = r[3]
                status = r[4] if len(r) > 4 else 0
                extra = f"  电量字节={lvl} ({lvl if lvl <= 100 else '?'}%)"
                if reg == 0x0D:
                    extra += f" 充电状态=0x{status:02X}"
            print(f"  槽位{n} {label:<17} -> {fmt(r)}   {err or ''}{extra}")

    print("\n" + "=" * 72)
    print("D. 触发设备重连 + 抓通知 reg0x02 写 0x02")
    print("=" * 72)
    r, err = tx(d, RECEIVER, req(0x02, 0x02, write=True))
    print(f"  写 reg0x02=0x02 -> {fmt(r)}  {err or ''}")
    time.sleep(0.3)
    for _ in range(10):
        rr = d.read(32, 200)
        if rr:
            print(f"  通知: {rr.hex(' ')}")
    d.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())