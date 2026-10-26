"""按 OpenLogi 权威规范核对 0x1004 的 fn0/fn1。

规范:
  fn0 get_battery_capabilities -> [byte0: 各级别支持位, byte1: rechargeable/percentage]
  fn1 get_battery_info         -> [byte0: charging_percentage, byte1: level(1/2/4/8),
                                   byte2: status(0..7), byte3: external_power, ...]
"""
import os
import sys

# --- 让 `python probes/xxx.py` 也能 import 到根目录的 hidpp ---
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hidpp import Gpw2Battery, list_interfaces, find_channels

b = Gpw2Battery()
if not b.open():
    print("打开失败:", b.last_error); sys.exit(1)

print("接口:")
sp, lp = find_channels()
print("  col01:", "有" if sp else "无")
print("  col02:", "有" if lp else "无")
print("接收器固件:", b.read_firmware(), " 连接数:", b.read_connection_count())

idx = b.resolve_battery_feature()
print(f"\nUNIFIED_BATTERY(0x1004) feature_index = {hex(idx) if idx else '未实现'}")

print("\n" + "=" * 70)
print("fn0 get_battery_capabilities  (发 [idx, 0x00, 0,0,0])")
print("=" * 70)
for i in range(3):
    r = b._h2(idx, 0x00)
    print(f"  #{i+1} {r.hex(' ') if r else 'None'}")
    if r and len(r) >= 6:
        print(f"      capabilities: byte0=0x{r[4]:02X} (级别位: crit={bool(r[4]&1)} "
              f"low={bool(r[4]&2)} good={bool(r[4]&4)} full={bool(r[4]&8)})")
        print(f"                    byte1=0x{r[5]:02X} (rechargeable={bool(r[5]&1)} "
              f"percentage={bool(r[5]&2)})")

print("\n" + "=" * 70)
print("fn1 get_battery_info  (发 [idx, 0x10, 0,0,0])")
print("=" * 70)
STATES = {0: "放电中", 1: "充电中", 2: "充电将满", 3: "已充满",
          4: "慢速充电", 5: "电池无效", 6: "过热", 7: "充电错误"}
LEVELS = {1: "Critical", 2: "Low", 4: "Good", 8: "Full"}
for i in range(3):
    r = b._h2(idx, 0x01)
    if r and len(r) >= 7:
        pct, lvl, st = r[4], r[5], r[6]
        print(f"  #{i+1} {r.hex(' ')}")
        print(f"      charging_percentage={pct}  level={lvl}({LEVELS.get(lvl,'?')})  "
              f"status={st}({STATES.get(st,'?')})")
    else:
        print(f"  #{i+1} {r.hex(' ') if r else 'None'}")

print("\n" + "=" * 70)
print("对照: 我原来发的 fn=0x00 解析成 percent -> 看两者差异")
print("=" * 70)
b.close()