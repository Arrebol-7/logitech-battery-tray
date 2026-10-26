"""设备在槽位 0x01，且是 HID++ 2.0 设备 -> 直接查 2.0 电池特性。

HID++ 2.0 报文格式（短报文 7B）:
    [0x10, dev, feature_index, function_id | sw_id(0x00), p1, p2, ...]

root 特性 index 恒为 0x00，特性 0x0000。它的功能:
    0x00 getFeature(feature_id[2B])   -> [feat_idx, feat_type, feat_version]
    0x01 getFeatureCount()            -> [count]
    0x10 getProtocolVersion()         -> [major, minor]

要查电池:
    1. 先用 root.getFeature(0x1000) 拿 BATTERY_VOLTAGE 的 index
    2. 再用 root.getFeature(0x1004) 拿 UNIFIED_BATTERY 的 index
    3. 然后 feature_idx.function 0 = getBatteryLevelStatus() / 0 = getVoltage()

错误码: 0x01 子命令无效 / 0x08 设备未知 / 0x09 资源错误 / 0x02 地址无效
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time

from probe_channels import Dev, list_vendor_paths, INVALID_HANDLE_VALUE

SHORT, LONG = 7, 20
SW_ID = 0x00  # software id, 放在 function 高 4 位

def fmt(r):
    return r.hex(" ") if r else "(无)"

def h2_call(d, dev, feat_idx, func, *params, long_msg=False):
    """发一条 HID++ 2.0 调用。

    短报文载荷: [feat_idx, (func<<4)|sw_id, p1, p2, p3]
    """
    payload = bytes([feat_idx, (func << 4) | SW_ID]) + bytes(params)
    size = LONG if (long_msg or len(payload) > 5) else SHORT
    report_id = 0x11 if size == LONG else 0x10
    msg = bytes([report_id, dev]) + payload
    msg = msg + bytes(size - len(msg))
    d.drain(60)
    wr = d.write(msg[:size], 400)
    if wr < 0:
        return None, f"write fail({wr})"
    deadline = time.time() + 0.8
    while time.time() < deadline:
        r = d.read(size if size == SHORT else 32, 200)
        if not r or r[0] not in (0x10, 0x11):
            continue
        if len(r) > 1 and r[1] != dev:
            continue
        return r, None
    return None, "timeout"

def root_get_feature(d, dev, feature_id):
    """root(0x0000).getFeature(feature_id) -> (feat_idx, feat_type, feat_version)"""
    r, err = h2_call(d, dev, 0x00, 0x00, (feature_id >> 8) & 0xFF, feature_id & 0xFF)
    if err or not r:
        return None, (r, err)
    if len(r) > 2 and r[2] == 0x8F:
        return None, (r, "错误响应")
    # 响应: [0x10, dev, feat_idx, (func<<4)|sw, ret_feat_idx, feat_type, feat_version]
    ret_idx = r[3]
    if ret_idx == 0x00:
        return None, (r, "该特性不存在(索引0)")
    return (ret_idx, r[4], r[5]), (r, None)

def main():
    paths = list_vendor_paths()
    paths.sort(key=lambda p: 0 if "col01" in p.lower() else 1)
    path = paths[0]
    d = Dev(path)
    if d.h == INVALID_HANDLE_VALUE:
        print("打开失败 err=", ctypes.get_last_error()); return 1

    DEV = 0x01  # 已确认设备在槽位 1

    print("=" * 74)
    print("1. root(0x0000) ping + 特性索引探测  dev=0x01")
    print("=" * 74)
    # root 只有两个功能: fn0 = getFeature, fn1 = ping (OpenLogi x0000-root 权威)
    r, err = h2_call(d, DEV, 0x00, 0x01, 0x55)   # ping(0x55)
    print(f"  ping(0x55)              -> {fmt(r)}  {err or ''}")
    if r and len(r) >= 4 and r[2] != 0x8F:
        print(f"     ping 回显 = 0x{r[3]:02X}  (应为 0x55)")
    r, err = h2_call(d, DEV, 0x00, 0x00, 0x00, 0x00, 0x00)  # getFeature(0x0000) 自指
    print(f"  getFeature(0x0000)      -> {fmt(r)}  {err or ''}")

    print("\n" + "=" * 74)
    print("2. 查电池特性 index")
    print("=" * 74)
    found = {}
    for fid, name in ((0x1000, "BATTERY_VOLTAGE"), (0x1004, "UNIFIED_BATTERY"),
                      (0x1001, "BATTERY_UNIT"), (0x1814, "CHANGE_HOST"),
                      (0x1812, "BRIGHTNESS")):
        info, raw = root_get_feature(d, DEV, fid)
        r, err = raw
        if info:
            idx, ftype, fver = info
            found[fid] = idx
            print(f"  {name:<18}(0x{fid:04X}) index=0x{idx:02X} type=0x{ftype:02X} ver={fver}   [{fmt(r)}]")
        else:
            print(f"  {name:<18}(0x{fid:04X}) 不可用: {err}   [{fmt(r)}]")

    print("\n" + "=" * 74)
    print("3. 读电量")
    print("=" * 74)
    if 0x1004 in found:
        idx = found[0x1004]
        r, err = h2_call(d, DEV, idx, 0x10)   # 0x1004.fn0 = getBatteryLevelStatus
        print(f"  UNIFIED_BATTERY(idx=0x{idx:02X}).fn0 getBatteryLevelStatus")
        print(f"    -> {fmt(r)}  {err or ''}")
        if r and len(r) >= 7 and r[2] != 0x8F:
            pct = r[3] & 0x7F
            charging = r[3] >> 7
            print(f"      电量 = {pct}%   充电中 = {charging}")
    if 0x1000 in found:
        idx = found[0x1000]
        r, err = h2_call(d, DEV, idx, 0x10)   # 0x1000.fn0 = getVoltage
        print(f"  BATTERY_VOLTAGE(idx=0x{idx:02X}).fn0 getVoltage")
        print(f"    -> {fmt(r)}  {err or ''}")
        if r and len(r) >= 6 and r[2] != 0x8F:
            mv = (r[3] << 8) | r[4]
            flags = r[5]
            print(f"      电压 = {mv} mV   状态 = 0x{flags:02X}")
    if 0x1001 in found:
        idx = found[0x1001]
        for fn, nm in ((0x00, "getBatteryUnit"),):
            r, err = h2_call(d, DEV, idx, fn)
            print(f"  BATTERY_UNIT(idx=0x{idx:02X}).fn{fn} {nm} -> {fmt(r)}  {err or ''}")
            if r and len(r) >= 4 and r[2] != 0x8F:
                units = {0x00: "绝对(mV/原始)", 0x01: "百分比", 0x02: "充电状态"}
                print(f"      单位 = {units.get(r[3], hex(r[3]))}")

    d.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())