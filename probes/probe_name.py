"""探测：设备名特性 0x0003 / 0x0005 的原始响应格式。

上一轮 probe_discovery 读 0x0003 fn0 时 r[4]=0x02 后还跟着 14 个非零字节，
fn1 读出的名字是乱码——字段布局与 Solaar 惯例不符，这里把原始字节全倒出来。
同时验证块偏移步进（16 字节块）与 getFeature 的 type/version 字节。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hidpp import HidDevice, find_channels
from probe_discovery import (list_all_hid_interfaces, try_firmware_exchange,
                             h20_call, get_feature)


def dump(tag, r):
    print(f"    {tag}: {r.hex(' ') if r else '(静默)'}")


def main():
    # 复用 probe_discovery 的识别逻辑找到接收器
    ifs = list_all_hid_interfaces()
    receivers = []
    for path, vid, pid in ifs:
        if vid != 0x046D:
            continue
        fw, d = try_firmware_exchange(path)
        if fw:
            receivers.append((vid, pid, d))
        elif d:
            d.close()
    if not receivers:
        print("无接收器")
        return 1

    for vid, pid, short in receivers:
        long = None
        for path, v2, p2 in ifs:
            if v2 == vid and p2 == pid and "mi_02" in path.lower() \
                    and "col02" in path.lower():
                long = HidDevice(path)
                break
        if not long or not long.ok:
            print("col02 打开失败")
            continue
        print(f"接收器 046D:{pid:04X}，扫描槽位…")
        for slot in range(1, 7):
            if h20_call(long, slot, [0x00, 0x01, 0x5A], tries=3, wait=0.35) is None:
                continue
            print(f"  槽位 {slot} 在线")
            for fid in (0x0003, 0x0005):
                r = h20_call(long, slot, [0x00, 0x00, (fid >> 8) & 0xFF, fid & 0xFF])
                if not r or 0x8F in (r[2], r[3]):
                    dump(f"getFeature(0x{fid:04X})", r)
                    continue
                idx = r[4]
                print(f"  特性 0x{fid:04X} → idx=0x{idx:02X}  "
                      f"完整响应: {r.hex(' ')}")
                r0 = h20_call(long, slot, [idx, 0x00])
                dump("fn0 (count)", r0)
                for off in (0, 16, 32):
                    r1 = h20_call(long, slot, [idx, 0x10, off])
                    dump(f"fn1 (offset={off})", r1)
                    if r1 and all(c == 0 for c in r1[4:]):
                        break
        long.close()
        short.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
