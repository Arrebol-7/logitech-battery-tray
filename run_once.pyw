"""桌面「查看鼠标电量」链接的目标：读一次电量并弹窗显示（无控制台窗口）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tkinter as tk
from tkinter import messagebox

from my_devices import read_all


def main():
    rows = read_all()
    root = tk.Tk()
    root.withdraw()
    if not rows:
        messagebox.showwarning("无线外设电量",
                               "读取失败。\n\n请确认：\n"
                               "· 接收器已插好\n· 鼠标已开机并动一下唤醒")
    else:
        lines = []
        for r in rows:
            p = f"{r['percent']}%" if r["percent"] is not None else r["level_text"]
            chg = "（充电中）" if r["charging"] else ""
            lines.append(f"{r['name']}\n    电量 {p}{chg}　{r['state_text']}")
        messagebox.showinfo("无线外设电量", "\n\n".join(lines))
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())