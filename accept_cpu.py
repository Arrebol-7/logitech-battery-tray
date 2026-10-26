"""验收标准 7：空闲时 CPU 占用接近 0（实测 75 秒内 < 0.5 秒 CPU）。

方法：启动托盘，等它完成首次读取后，取两次进程 CPU 时间快照，
间隔 75 秒，比较差值。用 GetProcessTimes（纯 ctypes，无需第三方库）。
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
THRESHOLD = 0.5      # 秒
DURATION = 75        # 秒

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD)]

    def seconds(self):
        return ((self.dwHighDateTime << 32) | self.dwLowDateTime) / 1e7


def proc_cpu(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        c, e, k, u = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if not kernel32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                        ctypes.byref(k), ctypes.byref(u)):
            return None
        return k.seconds() + u.seconds()
    finally:
        kernel32.CloseHandle(h)


def main():
    print("=" * 66)
    print(f"验收标准 7 —— 空闲 CPU 占用（{DURATION}s 内应 < {THRESHOLD}s）")
    print("=" * 66)
    p = subprocess.Popen([PY, os.path.join(HERE, "my_devices.py"), "--tray"],
                         cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        time.sleep(12)          # 等首次读取 + 图标绘制完成
        t0 = proc_cpu(p.pid)
        if t0 is None:
            print("✗ 无法读取进程 CPU 时间")
            return 1
        print(f"起始 CPU 时间: {t0:.4f}s —— 开始计时 {DURATION}s …")
        time.sleep(DURATION)
        t1 = proc_cpu(p.pid)
        if t1 is None:
            print("✗ 进程已退出？")
            return 1
        used = t1 - t0
        print(f"结束 CPU 时间: {t1:.4f}s")
        print(f"\n空闲 {DURATION}s 内消耗 CPU: {used:.4f}s  "
              f"({used / DURATION * 100:.3f}%)")
        ok = used < THRESHOLD
        print("\n结果:", f"✅ 验收标准 7 通过（< {THRESHOLD}s）" if ok
              else f"❌ 验收标准 7 失败（>= {THRESHOLD}s）")
        return 0 if ok else 1
    finally:
        p.terminate()
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()


if __name__ == "__main__":
    sys.exit(main())