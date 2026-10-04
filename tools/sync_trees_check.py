#!/usr/bin/env python3
"""双树一致性校验（2026-10-04）——防止 compat 与 兼容版/hermes 的共有脚本继续漂移。

背景：审计发现 compat 缺捕获链 12 文件（设计如此，捕获链依赖宿主 state.db），
但共有文件（四道门+lib）曾长期双头修改导致版本漂移（bak 文件群为证）。
本脚本默认只读：比对 compat 侧每个脚本与另两棵树同名文件的 md5，输出漂移清单；
--sync 把 compat 版本同步过去（目标树先打 .bak-同步前-<ts> 备份，绝不覆盖丢失）。

用法（在 compat 仓库根）:
  python tools/sync_trees_check.py            # 漂移报告
  python tools/sync_trees_check.py --sync     # 执行同步（带备份）
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

COMPAT = Path(__file__).resolve().parent.parent / "src" / "memory_ecology"
TARGETS = [
    Path.home() / "memory-ecology-兼容版" / "scripts",
    Path.home() / "AppData" / "Local" / "hermes" / "scripts",
]
# 捕获链/宿主专属：compat 没有属正常（不报缺失、不同步）
HOST_ONLY = {
    "eco_evolve.py", "eco_extract.py", "eco_git_commit.py", "eco_health_alert.py",
    "eco_l1_audit.py", "eco_note.py", "eco_note_backfill.py", "eco_note_backfill_runall.py",
    "eco_note_signals.py", "_ecoreview_selftest.py", "__init__.py",
}


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def sync_one(src: Path, targets: list[Path], ts: str, do_sync: bool, rel: str) -> tuple[int, int]:
    """返回 (漂移数, 缺失数)。"""
    drift = missing = 0
    c_hash = md5(src)
    for root in targets:
        t = root / rel
        if not t.exists():
            missing += 1
            if do_sync:
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, t)
                print(f"➕ {rel} → {root.parent.name}")
            else:
                print(f"➕ 缺失 {rel}（{root.parent.name}）")
            continue
        if md5(t) == c_hash:
            continue
        drift += 1
        if do_sync:
            bak = t.with_name(t.name + f".bak-同步前-{ts}")
            shutil.copy2(t, bak)
            shutil.copy2(src, t)
            print(f"🔁 {rel} → {root.parent.name}（备份 {bak.name}）")
        else:
            print(f"⚠️ {rel} 漂移（{root.parent.name}）")
    return drift, missing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", action="store_true", help="把 compat 版本同步到目标树（先备份）")
    args = ap.parse_args()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    drift = missing = 0
    for f in sorted(COMPAT.glob("*.py")):
        if f.name in HOST_ONLY:
            continue
        d, m = sync_one(f, TARGETS, ts, args.sync, f.name)
        drift += d
        missing += m
    for f in sorted((COMPAT / "lib").glob("*.py")):
        d, m = sync_one(f, TARGETS, ts, args.sync, f"lib/{f.name}")
        drift += d
        missing += m
    print("✅ 校验完成：无漂移" if (drift == 0 and missing == 0) else
          f"完成：漂移 {drift}、缺失 {missing}（--sync 执行同步）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
