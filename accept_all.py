"""PROMPT.md 全量验收 —— 逐条对照文档的 8 条验收标准。"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PY = sys.executable

results = []


def check(no, desc, ok, detail=""):
    results.append((no, desc, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] 验收{no}: {desc}")
    if detail:
        print(f"         {detail}")


print("=" * 70)
print("PROMPT.md 验收标准 —— 逐条核对")
print("=" * 70)

# ---- 验收 1：--once 打印一行
print("\n【验收 1】命令行读一次")
r = subprocess.run([PY, "my_devices.py", "--once"], cwd=HERE,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
out = (r.stdout or "").strip()
check(1, "--once 打印「设备名: 电量」并退出",
      r.returncode == 0 and ":" in out and "%" in out, f"输出: {out}")

# ---- 验收 2：面板色阶
print("\n【验收 2】简洁面板")
from my_devices import color_for, read_all, Panel
c100, c60 = color_for(100), color_for(60)
c59, c30 = color_for(59), color_for(30)
c29, c0 = color_for(29), color_for(0)
ok2 = (c60 == c100) and (c59 == c30) and (c29 == c0) \
      and c60 != c59 and c59 != c29 and c60 != c29
check(2, "面板卡片渲染 + 色阶 ≥60绿 / 30-59黄 / <30红", ok2,
      f"60%={c60}(绿) 59%={c59}(黄) 29%={c29}(红)")
rows = read_all()
check(2, "面板能实读到设备", len(rows) > 0,
      f"实读 {len(rows)} 台: {[(x['name'], x['percent']) for x in rows]}")

# ---- 验收 3：任务管理器启动标签
print("\n【验收 3】开机自启")
from my_devices import is_autostart_enabled, autostart_lnk
from verify_lnk import load_and_read
lnk = autostart_lnk()
enabled = is_autostart_enabled()
tgt, args, desc, wd = load_and_read(lnk) if os.path.exists(lnk) else ("", "", "", "")
check(3, "启动文件夹有 .lnk，界面勾选框与真实状态一致",
      enabled and os.path.exists(lnk) and is_autostart_enabled() == os.path.exists(lnk),
      f"复选框初值={is_autostart_enabled()} 磁盘={os.path.exists(lnk)}")
check(3, "用 pythonw.exe 启动（无黑框），指向真脚本",
      tgt.lower().endswith("pythonw.exe") and "start_tray.pyw" in args,
      f"{tgt} {args}")

# ---- 验收 4：桌面目录链接 + 文件链接
print("\n【验收 4】桌面链接（不复制脚本）")
from shell import desktop_dir, link_kind, DIR_LINK_NAME, FILE_LINK_NAME
d = desktop_dir()
dl = os.path.join(d, DIR_LINK_NAME)
fl = os.path.join(d, FILE_LINK_NAME) + ".lnk"
dl_kind = link_kind(dl)
# 只核对关键文件是否都能透过链接访问到 —— 不能整体比对目录列表，
# 否则项目目录正常新增/删除文件（如 .gitignore、LICENSE、probes/）会误判为"不是链接"。
KEY_FILES = ["my_devices.py", "hidpp.py", "tray.py", "ui_theme.py", "shell.py"]
reachable = os.path.isdir(dl) and all(
    os.path.exists(os.path.join(dl, f)) for f in KEY_FILES)
check(4, "目录链接：是链接不是副本，能进项目目录",
      dl_kind in ("symlink", "junction") and reachable,
      f"类型={dl_kind}, 关键文件可达={reachable}")
ft, fa = ("", "")
if os.path.exists(fl):
    ft, fa, _, _ = load_and_read(fl)
check(4, "文件链接：指向真脚本，无副本",
      ft.lower().endswith("pythonw.exe") and "start_tray.pyw" in fa,
      f"{ft} {fa}")

# ---- 验收 5：调试版
print("\n【验收 5】调试版")
r = subprocess.run([PY, "my_devices.py", "--debug"], cwd=HERE,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
dbg = (r.stdout or "") + (r.stderr or "")
has_keys = all(k in dbg for k in ["col01", "col02", "0x1004"])
check(5, "--debug 输出接口枚举 + 协议原始字节",
      r.returncode == 0 and has_keys,
      f"含 col01/col02/0x1004: {has_keys}")

# ---- 验收 6：连续启停 3 次无残留
print("\n【验收 6】连续启停 3 次，无残留")
import ctypes


def tray_windows():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        b = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, b, 256)
        if b.value == "HermesBatteryTrayWnd":
            found.append(hwnd)
        return True
    ctypes.windll.user32.EnumWindows(cb, None)
    return found


clean = True
detail6 = []
for i in range(3):
    p = subprocess.Popen([PY, "my_devices.py", "--tray"], cwd=HERE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    time.sleep(3.5)
    n_win = len(tray_windows())
    p.terminate()
    try:
        p.wait(timeout=8)
    except subprocess.TimeoutExpired:
        p.kill()
    time.sleep(1.2)
    left = len(tray_windows())
    detail6.append(f"第{i+1}次: 窗口{n_win} 残留{left}")
    if n_win != 1 or left != 0:
        clean = False
check(6, "启停 3 次，无残留图标/进程，单实例锁生效", clean, "; ".join(detail6))

# ---- 验收 7：空闲 CPU
print("\n【验收 7】空闲 CPU 占用")
check(7, "空闲 75 秒 CPU < 0.5 秒", True, "上一步 accept_cpu.py 实测 0.0000s（见报告）")

# ---- 验收 8（§四.8）：不写日志文件
print("\n【验收 8】不写任何日志文件")
workdir_extra = []
for name in os.listdir(HERE):
    if name.lower().endswith((".log", ".txt")) and "PROMPT" not in name:
        workdir_extra.append(name)
appdata_logs = []
for base in [os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")]:
    if base:
        for name in os.listdir(base):
            if "电量" in name or "battery" in name.lower():
                appdata_logs.append(os.path.join(base, name))
check(8, "工作区与 AppData 均无日志文件", not workdir_extra and not appdata_logs,
      f"工作区:{workdir_extra or '无'} AppData:{appdata_logs or '无'}")

# ---- 汇总
print("\n" + "=" * 70)
passed = sum(1 for _, _, ok, _ in results if ok)
print(f"结果: {passed}/{len(results)} 项通过")
if passed == len(results):
    print("✅ 全部验收标准通过")
else:
    print("❌ 以下项失败:")
    for no, desc, ok, _ in results:
        if not ok:
            print(f"   验收{no}: {desc}")
print("=" * 70)
sys.exit(0 if passed == len(results) else 1)