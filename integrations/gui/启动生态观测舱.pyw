#!/usr/bin/env python3
"""观测舱双击入口（.pyw → pythonw 运行：无控制台黑框）。

与 启动生态观测舱.cmd 等价的第三入口：
- 服务已在本机 8788 运行 → eco_gui 直接再开一个新窗口（不产生第二个服务）
- 未运行 → 起服务 + 开原生窗口（pywebview）或系统浏览器
- 启动失败 → ctypes MessageBoxW 弹出原因（pythonw 下没有控制台可看）
相对路径（开源友好）；桌面副本由 install_desktop.py 生成，不在本文件写绝对路径。
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _alert(text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "观测舱启动失败", 0x10)  # MB_ICONERROR
    except Exception:
        pass


def main() -> int:
    try:
        import eco_gui
        sys.argv = [eco_gui.__file__]
        return eco_gui.main() or 0
    except Exception:
        _alert("启动 eco_gui.py 失败：\n\n" + traceback.format_exc(limit=5)
               + "\n可先运行同目录 生态急救箱.cmd 自检修复。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
