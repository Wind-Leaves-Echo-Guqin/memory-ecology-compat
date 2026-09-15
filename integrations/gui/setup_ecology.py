#!/usr/bin/env python3
"""记忆生态 · 观测舱 —— 一键安装/设置（T15）。

做四件事，每步都可单独跳过，失败不搞坏环境：
  1. 体检环境：Python 版本（≥3.10）、数据根、脚本目录
  2. 可选依赖：pywebview（原生窗口）/ pystray+Pillow（托盘常驻）——缺失只提示不强制
  3. 端口探测：默认 8788，被占用自动上扫（最多 +20）
  4. 生成桌面双击入口（复用 install_desktop.py 的指针式逻辑）
  5. 首跑自检：无窗口起服务 → 请求 /api/meta → 校验 ok/search_cli_ready → 收工

用法:
  python setup_ecology.py              # 交互式（会询问是否安装可选依赖）
  python setup_ecology.py --yes        # 全自动，不询问（默认不装可选依赖）
  python setup_ecology.py --no-desktop # 不生成桌面入口
  python setup_ecology.py --no-verify  # 不做首跑自检
  python setup_ecology.py --port 8899  # 指定端口
"""
from __future__ import annotations

import argparse
import json
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
MIN_PY = (3, 10)
DEFAULT_PORT = 8788
OPTIONAL_DEPS = {
    "webview": ("pywebview", "原生窗口（缺省时自动降级为系统浏览器）"),
    "pystray": ("pystray", "系统托盘常驻（缺省时无托盘，其余功能不受影响）"),
    "PIL": ("Pillow", "托盘图标绘制（pystray 的依赖）"),
}


def say(icon: str, msg: str) -> None:
    print(f"{icon} {msg}")


def step_env() -> bool:
    print("── 1/5 环境体检 ──")
    ok = True
    if sys.version_info < MIN_PY:
        say("❌", f"Python {sys.version.split()[0]} 过低，需要 ≥{MIN_PY[0]}.{MIN_PY[1]}")
        say("   ", "请到 https://www.python.org/downloads/ 安装新版后重跑本脚本")
        return False
    say("✅", f"Python {sys.version.split()[0]}（要求 ≥{MIN_PY[0]}.{MIN_PY[1]}）")
    if not (HERE / "eco_gui.py").is_file():
        say("❌", f"未找到 eco_gui.py（本脚本应在 integrations/gui/ 下运行）：{HERE}")
        return False
    say("✅", f"观测舱目录：{HERE}")
    return ok


def step_deps(auto_yes: bool) -> None:
    print("\n── 2/5 可选依赖 ──")
    missing = []
    for mod, (pkg, why) in OPTIONAL_DEPS.items():
        try:
            __import__(mod)
            say("✅", f"{pkg} 已安装（{why}）")
        except ImportError:
            missing.append((pkg, why))
    if not missing:
        return
    for pkg, why in missing:
        say("•", f"缺少 {pkg} —— {why}")
    if auto_yes:
        say("→", "非交互模式：跳过安装（需要时手动执行 pip install " +
            " ".join(p for p, _ in missing) + "）")
        return
    try:
        ans = input("是否现在安装以上可选依赖？[y/N] ").strip().lower()
    except EOFError:
        ans = "n"
    if ans != "y":
        say("→", "已跳过。缺 pywebview 时启动会自动改用系统浏览器打开，功能不受影响。")
        return
    pkgs = [p for p, _ in missing]
    say("→", "执行：" + sys.executable + " -m pip install " + " ".join(pkgs))
    rc = subprocess.call([sys.executable, "-m", "pip", "install", *pkgs])
    say("✅" if rc == 0 else "⚠", "依赖安装" + ("完成" if rc == 0 else f"失败（退出码 {rc}），可稍后手动安装"))


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def step_port(preferred: int) -> int:
    print("\n── 3/5 端口探测 ──")
    for port in range(preferred, preferred + 21):
        if port_free(port):
            say("✅", f"可用端口 {port}" + ("" if port == preferred else f"（{preferred} 已被占用，自动上扫）"))
            return port
    say("⚠", f"{preferred}~{preferred + 20} 均被占用，仍按 {preferred} 继续（服务端会自行上扫）")
    return preferred


def step_desktop(enabled: bool) -> None:
    print("\n── 4/5 桌面入口 ──")
    if not enabled:
        say("→", "已跳过（--no-desktop）")
        return
    try:
        import install_desktop
        n = install_desktop.install(pathlib.Path.home() / "Desktop")
        if n:
            say("✅", f"桌面入口已生成（{n} 个，指针式，仓库更新后自动生效）")
        else:
            say("⚠", "桌面入口未生成（详见上方提示）")
    except Exception as e:  # noqa: BLE001 - 安装失败不应中断流程
        say("⚠", f"桌面入口生成失败（不影响启动）：{e}")


def step_verify(port: int, skip: bool) -> bool:
    print("\n── 5/5 首跑自检 ──")
    if skip:
        say("→", "已跳过（--no-verify）")
        return True
    cmd = [sys.executable, str(HERE / "eco_gui.py"), "--port", str(port),
           "--no-window", "--no-browser"]
    say("→", "启动服务：" + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/api/meta"
    try:
        for attempt in range(12):          # 最多等 ~6 秒
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    meta = json.loads(r.read().decode("utf-8"))
                say("✅", f"服务响应正常：ok={meta.get('ok')} · 数据根={meta.get('root')}")
                say("✅" if meta.get("search_cli_ready") else "⚠",
                    f"检索 CLI：{'就绪' if meta.get('search_cli_ready') else '未找到（检索不可用，其余功能正常）'}")
                say("✅", f"生态版本 {meta.get('version')} · 评分模型 {meta.get('score_model')}")
                return True
            except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
                continue
        say("⚠", f"服务在 6 秒内未就绪（{url}）——可手动启动排查：{' '.join(cmd)}")
        return False
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        say("→", "自检服务已停止（日常使用请走桌面入口或启动器）")


def main() -> int:
    ap = argparse.ArgumentParser(description="记忆生态 · 观测舱 一键安装/设置")
    ap.add_argument("--yes", action="store_true", help="全自动，不询问（默认不装可选依赖）")
    ap.add_argument("--no-desktop", action="store_true", help="不生成桌面入口")
    ap.add_argument("--no-verify", action="store_true", help="不做首跑自检")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args()

    print("╭──────────────────────────────────────────────╮")
    print("│ 记忆生态 · 观测舱 · 一键安装/设置            │")
    print("╰──────────────────────────────────────────────╯")
    if not step_env():
        return 1
    step_deps(a.yes)
    port = step_port(a.port)
    step_desktop(not a.no_desktop)
    ok = step_verify(port, a.no_verify)

    print("\n" + ("✅ 安装完成，双击桌面「启动生态观测舱」即可使用。" if ok
                 else "⚠ 安装基本完成，但自检未通过——请按上方提示排查。"))
    print("   托盘常驻（可选）：eco_gui.py --tray    开机自启开关在托盘右键菜单里")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
