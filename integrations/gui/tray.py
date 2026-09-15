#!/usr/bin/env python3
"""记忆生态 · 观测舱 —— 系统托盘常驻 + 开机自启（T16）。

设计取舍：
  · 托盘依赖 pystray + Pillow（可选依赖，缺失时本模块只打印指引并返回，
    不阻塞服务启动——观测舱主体功能不依赖托盘）
  · 窗口显示/隐藏走 pywebview；未装 pywebview 时托盘只剩「打开浏览器 / 退出」
  · 开机自启写 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run（当前用户级，
    不需要管理员权限，可随时从菜单关掉）
  · 两个开关的落盘位置：%APPDATA%\\eco-gui\\settings.json

用法:
  from tray import Tray
  Tray(port=8788, on_quit=...).run()      # 阻塞在托盘消息循环
  Tray.autostart_enable() / autostart_disable() / autostart_enabled()
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

APP_NAME = "记忆生态观测舱"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SETTINGS_DIR = pathlib.Path(os.environ.get("APPDATA", str(pathlib.Path.home()))) / "eco-gui"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"
HERE = pathlib.Path(__file__).resolve().parent


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _autostart_cmd(port: int) -> str:
    """开机自启命令：pythonw 无窗口起服务 + 托盘（无 pythonw 时退回 python）。"""
    exe = pathlib.Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    runner = pyw if pyw.is_file() else exe
    return f'"{runner}" "{HERE / "eco_gui.py"}" --port {port} --tray --no-browser'


def autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(value)
    except (OSError, ImportError):
        return False


def autostart_enable(port: int = 8788) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "仅 Windows 支持注册表自启"
    try:
        import winreg
        cmd = _autostart_cmd(port)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
        s = load_settings()
        s.update({"autostart": True, "port": port})
        save_settings(s)
        return True, cmd
    except (OSError, ImportError) as e:
        return False, str(e)


def autostart_disable() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "仅 Windows 支持注册表自启"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
    except FileNotFoundError:
        pass
    except (OSError, ImportError) as e:
        return False, str(e)
    s = load_settings()
    s["autostart"] = False
    save_settings(s)
    return True, "已关闭开机自启"


def _icon_image():
    """生成 16×16/64×64 托盘图标（绿点 + 环形，与 favicon 同语言）。无 Pillow 返回 None。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 4, size - 4, size - 4], fill=(14, 116, 144, 255))
    d.ellipse([size // 2 - 9, size // 2 - 9, size // 2 + 9, size // 2 + 9], fill=(255, 255, 255, 255))
    return img


class Tray:
    """托盘控制器。run() 阻塞；未装 pystray 时给出可读指引并立即返回 False。"""

    def __init__(self, port: int = 8788, url: str | None = None, on_quit=None):
        self.port = port
        self.url = url or f"http://127.0.0.1:{port}"
        self.on_quit = on_quit
        self.icon = None
        self.window = None

    # ── 窗口 ──
    def show_window(self):
        if self.window is not None:
            try:
                self.window.show()
                return
            except Exception:  # noqa: BLE001 - 窗口可能已被销毁
                self.window = None
        self.open_browser()

    def hide_window(self):
        if self.window is not None:
            try:
                self.window.hide()
                return
            except Exception:  # noqa: BLE001
                pass
        self._notify("窗口", "无可隐藏的窗口（当前为浏览器模式）")

    def open_browser(self):
        import webbrowser
        webbrowser.open(self.url)

    def _notify(self, title: str, msg: str):
        if self.icon is not None:
            try:
                self.icon.notify(msg, title)
            except Exception:  # noqa: BLE001
                pass

    # ── 托盘 ──
    def run(self) -> bool:
        try:
            import pystray
        except ImportError:
            print("⚠ 未安装 pystray —— 托盘常驻不可用（不影响观测舱功能）。")
            print(f"  安装后可用：{sys.executable} -m pip install pystray Pillow")
            print(f"  或直接开浏览器使用：{self.url}")
            return False
        img = _icon_image()
        if img is None:
            print("⚠ 托盘需要 Pillow（图标绘制）。安装：python -m pip install Pillow")
            return False

        autostart = autostart_enabled()
        menu = pystray.Menu(
            pystray.MenuItem("显示观测舱", lambda: self.show_window(), default=True),
            pystray.MenuItem("在浏览器中打开", lambda: self.open_browser()),
            pystray.MenuItem("隐藏窗口", lambda: self.hide_window()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("开机自启（可选，默认关）", self._toggle_autostart, checked=lambda i: autostart_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出（停止服务）", self._quit),
        )
        self.icon = pystray.Icon(APP_NAME, img, f"{APP_NAME} · {self.url}", menu)
        print(f"✅ 托盘已启动（{APP_NAME}）· 左键显示/隐藏 · 右键菜单可开关开机自启")
        self.icon.run()
        return True

    def _toggle_autostart(self, *_: object) -> None:
        if autostart_enabled():
            ok, msg = autostart_disable()
            self._notify("开机自启", "已关闭" if ok else f"关闭失败：{msg}")
        else:
            ok, msg = autostart_enable(self.port)
            self._notify("开机自启", "已开启" if ok else f"开启失败：{msg}")

    def _quit(self, *_: object) -> None:
        try:
            if self.icon is not None:
                self.icon.stop()
        finally:
            if self.on_quit:
                self.on_quit()


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=f"{APP_NAME} 托盘")
    ap.add_argument("--port", type=int, default=load_settings().get("port", 8788))
    ap.add_argument("--autostart", choices=["on", "off", "status"], help="直接操作开机自启开关")
    a = ap.parse_args()
    if a.autostart == "on":
        print(autostart_enable(a.port))
        return 0
    if a.autostart == "off":
        print(autostart_disable())
        return 0
    if a.autostart == "status":
        print("开机自启：", "已开启" if autostart_enabled() else "已关闭")
        return 0
    t = Tray(port=a.port)
    return 0 if t.run() else 1


if __name__ == "__main__":
    sys.exit(main())
