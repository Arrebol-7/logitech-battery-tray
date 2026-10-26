"""最终探测：用 col02 长报文通道 + 唤醒重试，读 GPW2 电量。

已确认（无争议）:
    col01 (7B)  = 短报文通道, 只能发 0x10, 用于接收器自身 1.0 寄存器
    col02 (20B) = 长报文通道, 发 0x11 能收到 20B 响应, 用于转发给设备

HID++ 2.0 报文:
    [0x11, dev, feat_idx, (fn<<4)|sw_id, p1, p2, ...]
    root(0x0000) index 恒为 0:
        fn0 getFeature(u16 id)  -> [feat_idx, type, version]
        fn1 ping(u8 data)       -> 回显 data

电池特性:
    0x1000 BATTERY_VOLTAGE   fn0 getVoltage()      -> mV(2B) + flags
    0x1004 UNIFIED_BATTERY   fn0 getBatteryLevelStatus() -> [level(bit7=charging), next, ...]
    0x1001 BATTERY_UNIT      fn0 getBatteryUnit()  -> unit

策略: 每个请求重试 N 次（唤醒无线设备），只认 dev 匹配且非回显的响应。
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time

from probe_channels import Dev, list_vendor_paths, INVALID_HANDLE_VALUE

DEV = 0x01

def fmt(r):
    return r.hex(" ") if r else "(无)"

def call(dev_obj, dev, payload, report_id, size, tries=4, wait=0.4):
    """发报文并读响应，重试 tries 次以唤醒无线设备。"""
    results = []
    for attempt in range(tries):
        msg = bytes([report_id, dev]) + bytes(payload)
        msg = msg + bytes(size - len(msg))
        dev_obj.drain(50)
        wr = dev_obj.write(msg[:size], 500)
        if wr < 0:
            results.append(("write fail", None))
            continue
        deadline = time.time() + wait
        got = None
        while time.time() < deadline:
            r = dev_obj.read(32, 150)
            if not r or r[0] not in (0x10, 0x11):
                continue
            if len(r) > 1 and r[1] != dev:
                continue
            got = r
            break
        if got is not None:
            results.append(("ok", got))
            # 判断是不是"纯回显"（前几字节等于我发的 payload）
            body = got[2:2 + len(payload)]
            if body[:min(3, len(payload))] == bytes(payload)[:min(3, len(payload))]:
                continue  # 回显，继续重试等真响应
            return got, f"ok(第{attempt+1}次)"
        time.sleep(0.15)
    if results:
        return results[-1][1], f"仅回显/失败({len(results)}次)"
    return None, "timeout"

def main():
    paths = list_vendor_paths()
    col01 = next((p for p in paths if "col01" in p.lower()), None)
    col02 = next((p for p in paths if "col02" in p.lower()), None)
    print("接口:")
    print("  col01 =", col01.split("#")[-1][:50] if col01 else "(无)")
    print("  col02 =", col02.split("#")[-1][:50] if col02 else "(无)")
    if not col02:
        print("没有 col02, 放弃"); return 1

    dl = Dev(col02)          # 长报文通道
    if dl.h == INVALID_HANDLE_VALUE:
        print("col02 打开失败 err=", ctypes.get_last_error()); return 1

    print("\n" + "=" * 74)
    print("1. root ping (唤醒设备)  dev=0x01  [col02 长报文]")
    print("=" * 74)
    for data in (0x55, 0xAA, 0x00):
        r, note = call(dl, DEV, bytes([0x00, 0x10, data]) + bytes(15), 0x11, 20)
        echo = ""
        if r and len(r) > 4:
            echo = f"  payload[2:5]={r[2:5].hex(' ')}"
        print(f"  ping(0x{data:02X}) -> {fmt(r)}   {note}{echo}")

    print("\n" + "=" * 74)
    print("2. 查电池特性 index  dev=0x01")
    print("=" * 74)
    found = {}
    for fid, name in ((0x1000, "BATTERY_VOLTAGE"), (0x1004, "UNIFIED_BATTERY"),
                      (0x1001, "BATTERY_UNIT"), (0x0003, "DEVICE_NAME"),
                      (0x0000, "root(自指)")):
        r, note = call(dl, DEV, bytes([0x00, 0x00, (fid >> 8) & 0xFF, fid & 0xFF]) + bytes(14),
                       0x11, 20, tries=5)
        info = ""
        if r and len(r) >= 6 and r[2] != 0x8F:
            ret_idx = r[4]
            ftype = r[5]
            fver = r[6] if len(r) > 6 else 0
            if ret_idx != 0x00 or fid == 0x0000:
                found[fid] = ret_idx
                info = f"  -> feature_index=0x{ret_idx:02X} type=0x{ftype:02X} ver={fver}"
        print(f"  {name:<18}(0x{fid:04X}) -> {fmt(r)[:62]}  {note}{info}")

    print("\n" + "=" * 74)
    print("3. 读电量")
    print("=" * 74)
    if 0x1004 in found:
        idx = found[0x1004]
        r, note = call(dl, DEV, bytes([idx, 0x00]) + bytes(16), 0x11, 20, tries=6)
        print(f"  UNIFIED_BATTERY(idx=0x{idx:02X}).fn0 getBatteryLevelStatus")
        print(f"    -> {fmt(r)}   {note}")
        if r and len(r) >= 4 and r[2] != 0x8F:
            lvl = r[2]
            print(f"      ★ 电量 = {lvl & 0x7F}%   充电中 = {bool(lvl >> 7)}")
    if 0x1000 in found:
        idx = found[0x1000]
        r, note = call(dl, DEV, bytes([idx, 0x00]) + bytes(16), 0x11, 20, tries=6)
        print(f"  BATTERY_VOLTAGE(idx=0x{idx:02X}).fn0 getVoltage")
        print(f"    -> {fmt(r)}   {note}")
        if r and len(r) >= 5 and r[2] != 0x8F:
            mv = (r[2] << 8) | r[3]
            print(f"      ★ 电压 = {mv} mV   状态 = 0x{r[4]:02X}")
    if 0x1001 in found:
        idx = found[0x1001]
        r, note = call(dl, DEV, bytes([idx, 0x00]) + bytes(16), 0x11, 20, tries=4)
        print(f"  BATTERY_UNIT(idx=0x{idx:02X}).fn0 getBatteryUnit -> {fmt(r)}  {note}")
        if r and r[2] != 0x8F:
            units = {0x00: "绝对", 0x01: "百分比", 0x02: "充电状态", 0x03: "mV", 0x04: "mWh"}
            print(f"      单位 = {units.get(r[2], hex(r[2]))}")

    print("\n" + "=" * 74)
    print("4. 1.0 兜底: 接收器寄存器直读 (dev=0xFF, col01 短报文)")
    print("=" * 74)
    ds = Dev(col01)
    if ds.h != INVALID_HANDLE_VALUE:
        for reg, name in ((0x02, "连接状态"), (0x00, "通知标志")):
            msg = bytes([0x10, 0xFF, 0x81, reg, 0x00, 0x00, 0x00])
            ds.drain(50); ds.write(msg, 400)
            r = ds.read(7, 400)
            print(f"  reg0x{reg:02X} {name} -> {fmt(r)}")
        ds.close()

    dl.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())