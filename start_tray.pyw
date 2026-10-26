"""开机自启入口：无控制台窗口启动托盘。由「电量托盘」快捷方式调用。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from my_devices import main

if __name__ == "__main__":
    # 无参数调用时默认起托盘（快捷方式不再重复传 --tray）
    sys.exit(main(sys.argv[1:] or ["--tray"]))