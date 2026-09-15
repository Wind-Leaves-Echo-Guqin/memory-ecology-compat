#!/usr/bin/env python3
"""生成桌面双击入口（本机部署产物；生成物勿提交仓库）。

为什么需要它：仓库内启动 cmd 全部使用 %~dp0 相对路径（任何 clone 位置可用，
开源友好）；桌面副本则必须指向本机绝对路径。历史上桌面副本靠手工复制同步，
出过两类事故：路径拼接错误（急救箱双击即失败且伪装成功）与编码分叉
（GBK/UTF-8 各一份内容漂移）。本脚本按当前机器生成正确的桌面入口：

  桌面/启动生态观测舱.cmd → call <仓库>/integrations/gui/启动生态观测舱.cmd
  桌面/生态急救箱.cmd     → call <仓库>/integrations/gui/生态急救箱.cmd

生成物是两行指针：全部逻辑仍在仓库正式版内（单一来源），仓库更新即自动生效。
编码策略：优先系统 ANSI 代码页（默认 cmd 可直接解析中文路径）；路径含当前
代码页无法编码的字符时，回退 UTF-8 + chcp 65001 包装。

用法:
  python install_desktop.py             # 生成/覆盖桌面两个入口
  python install_desktop.py --dir DIR   # 指定目标目录（默认桌面）
  python install_desktop.py --remove    # 删除生成的桌面入口
"""
from __future__ import annotations

import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
LAUNCHERS = ("启动生态观测舱.cmd", "生态急救箱.cmd")


def _write_pointer(dst: pathlib.Path, target: pathlib.Path) -> str:
    path = str(target)
    try:
        # cmd 解析批处理用的是系统 ANSI 代码页（中文 Windows=GBK），不是
        # Python 的 locale 编码（UTF-8 模式下恒为 utf-8，会写出 cmd 读不懂的文件）
        path.encode("mbcs")
        body = '@echo off\r\ncall "' + path + '"\r\n'
        dst.write_bytes(body.encode("mbcs"))
        return "ansi"
    except (UnicodeEncodeError, LookupError):
        body = '@echo off\r\nchcp 65001 >nul\r\ncall "' + path + '"\r\n'
        dst.write_bytes(body.encode("utf-8"))
        return "utf-8 + chcp 65001"


def install(target: pathlib.Path) -> int:
    n = 0
    for name in LAUNCHERS:
        src = HERE / name
        if not src.is_file():
            print(f"❌ 仓库内缺少 {src}，跳过（先补齐启动器再重跑）")
            continue
        enc = _write_pointer(target / name, src)
        print(f"✅ {target / name}  → 指向 {src}（{enc}）")
        n += 1
    return n


def remove(target: pathlib.Path) -> None:
    for name in LAUNCHERS:
        p = target / name
        if p.is_file():
            p.unlink()
            print(f"🗑 已删除 {p}")
        else:
            print(f"— 不存在 {p}")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成/删除桌面双击入口（指针式，指向仓库正式启动器）")
    ap.add_argument("--dir", type=pathlib.Path, default=pathlib.Path.home() / "Desktop")
    ap.add_argument("--remove", action="store_true", help="删除桌面生成的入口")
    a = ap.parse_args()
    if not a.dir.is_dir():
        print(f"❌ 目标目录不存在：{a.dir}")
        return 1
    if a.remove:
        remove(a.dir)
        return 0
    if install(a.dir) < len(LAUNCHERS):
        return 1
    print("\n完成。双击桌面入口即可使用；仓库内启动器更新后无需重新生成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
