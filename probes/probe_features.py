"""枚举 HID++ 特性表：先读 feature count，再逐个读 feature ID，找电量相关特性。

通道已确认：mi_02&col01 / Report ID 0x10 / 短报文 7B。
root feature = 0x0000，功能：
  0x00 getFeature(id)      -> index, type, version
  0x01 getFeatureCount()   -> count
  0x10 getFeatureId(index) -> featureId
"""
import ctypes
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctypes import wintypes

from probe_channels import (
    list_vendor_paths, Dev,
)

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

SHORT = 0x10

# 已知电量/电池相关特性
BATTERY_FEATURES = {
    0x1000: "BATTERY_VOLTAGE (旧)",
    0x1001: "BATTERY_STATUS / Battery Level Status",
    0x1004: "UNIFIED_BATTERY",
    0x1300: "BATTERY_VOLTAGE (新) ",
    0x1814: "CHANGE_HOST",
    0x1E00: "BATTERY_VOLTAGE_2?",
    0x1002: "BATTERY? (未确认)",
}

# 接收器/设备通用特性
KNOWN = {
    0x0000: "ROOT", 0x0001: "FEATURE_SET", 0x0003: "DEVICE_FW_VERSION",
    0x0005: "DEVICE_NAME", 0x0020: "CONFIG_CHANGE", 0x0021: "CRYPTO_ID",
    0x1000: "BATTERY_VOLTAGE", 0x1001: "BATTERY_STATUS",
    0x1004: "UNIFIED_BATTERY", 0x1300: "BATTERY_VOLTAGE2",
    0x1802: "DEVICE_TYPE_AND_NAME", 0x1814: "CHANGE_HOST",
    0x1815: "HOSTS_INFO", 0x1816: "?",
    0x1B04: "SPECIAL_KEYS_BUTTONS", 0x1E00: "?",
    0x2100: "VERTICAL_SCROLLING", 0x2120: "?",
    0x8000: "? (vendor)", 0x8100: "ONBOARD_PROFILES",
    0x8150: "MOUSE_BUTTON_SPY", 0x8040: "?",
    0x1300: "BATTERY_VOLTAGE_2", 0x1F20: "ADC_MEASUREMENT",
}

def call(dev, dev_index, feat_index, function, params=b"", size=7, timeout=800):
    msg = bytes([SHORT, dev_index, feat_index, function]) + params
    msg = msg + bytes(size - len(msg))
    wr = dev.write(msg[:size])
    if wr < 0:
        return None, f"write fail({wr})"
    r = dev.read(size, timeout)
    if r is None:
        return None, "timeout"
    return r, None

def main():
    paths = list_vendor_paths()
    # col01 才是能收发的那个
    col01 = [p for p in paths if "col01" in p.lower()]
    if not col01:
        print("没找到 col01 接口")
        return 1
    path = col01[0]
    print("使用接口:", path.split("&")[2] + "&col01")
    dev = Dev(path)
    if dev.h == INVALID_HANDLE_VALUE:
        print("打开失败")
        return 2

    try:
        for dev_index in (0x01, 0xFF):
            print(f"\n{'='*64}\n=== dev_index = 0x{dev_index:02X} ===")
            # root getFeatureCount
            r, err = call(dev, dev_index, 0x00, 0x01)
            if r is None:
                print("  getFeatureCount 失败:", err)
                continue
            print(f"  getFeatureCount raw: {r.hex(' ')}")
            if r[3] not in (0x01,):
                if r[3] == 0x8F:
                    print(f"  -> HID++ 错误: {r.hex(' ')}")
                    continue
            count = r[4]
            print(f"  特性数量: {count}")

            if count == 0 or count > 64:
                print("  数量异常，跳过")
                continue

            feats = []
            for i in range(count):
                r2, err2 = call(dev, dev_index, 0x00, 0x10, bytes([i]))
                if r2 is None:
                    continue
                fid = int.from_bytes(r2[4:6], "big")
                feats.append(fid)

            print(f"  枚举到 {len(feats)} 个特性:")
            for fid in feats:
                name = KNOWN.get(fid, "")
                mark = "  <<< 电量相关!" if fid in BATTERY_FEATURES else ""
                print(f"    0x{fid:04X}  {name}{mark}")

            # 对每个电量相关特性，试读数值
            for fid in feats:
                if fid not in BATTERY_FEATURES:
                    continue
                print(f"\n  --- 读取 0x{fid:04X} {BATTERY_FEATURES[fid]} ---")
                r3, err3 = call(dev, dev_index, 0x00, 0x00,
                                bytes([fid >> 8, fid & 0xFF]))
                if r3 is None:
                    print("    getFeature 失败:", err3); continue
                if r3[3] == 0x8F:
                    print(f"    错误: {r3.hex(' ')}"); continue
                fidx = r3[1]
                print(f"    feature_index=0x{fidx:02X} type=0x{r3[4]:02X} ver={r3[5]}")
                if fidx == 0:
                    print("    -> index 0，特性不可用"); continue
                for func, label, size in ((0x00, "getStatus/getVoltage", 7),
                                          (0x10, "getCapabilities", 7),
                                          (0x11, "getStatusExt", 7)):
                    rr, ee = call(dev, dev_index, fidx, func, b"", size)
                    if rr:
                        print(f"    fn 0x{func:02X} ({label}): {rr.hex(' ')}")
    finally:
        dev.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())