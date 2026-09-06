#!/usr/bin/env python3
"""生态基因库每日快照（memory-skill-ecosystem 项目 3.1 节）。

把 memories/ skills/ scripts/eco_health_check.py 增量同步进
数据根下 ecosystem.git/（默认跟随宿主数据根），designs/workflows 每日只读快照进
archive/<日期>/，有变化则 git commit（无变化跳过）。

验收标准：连续 7 天 auto-commit 实际落盘。
用法: python eco_git_commit.py   （cron 每日 12:30 no_agent）
"""
import datetime
import shutil
import subprocess
import sys
from pathlib import Path

from lib.config import hermes_root

HERMES = hermes_root()
REPO = HERMES / "ecosystem.git"
AGENT_INDEX = Path.home() / ".memory-ecology"
TODAY = datetime.date.today().isoformat()

SKIP_SKILLS = {".hub"}  # 40MB 缓存，不进基因库


def sync_tree(src: Path, dst: Path, skip: set[str] | None = None) -> bool:
    """增量同步 src -> dst；返回是否有变化。"""
    skip = skip or set()
    changed = False
    if not src.exists():
        return changed
    dst.mkdir(parents=True, exist_ok=True)
    # 删除目标里源已不存在的条目（保持镜像）——**绝不碰 .git**
    for item in dst.iterdir():
        if item.name in skip or item.name == ".git":
            continue
        if not (src / item.name).exists():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            changed = True
    # 增量复制
    for item in src.iterdir():
        if item.name in skip:
            continue
        target = dst / item.name
        if item.is_dir():
            if sync_tree(item, target):
                changed = True
        else:
            try:
                src_m = item.stat().st_mtime_ns
                dst_m = target.stat().st_mtime_ns if target.exists() else -1
                if src_m != dst_m:
                    shutil.copy2(item, target)
                    changed = True
            except OSError:
                shutil.copy2(item, target)
                changed = True
    return changed


def main() -> int:
    if not (REPO / ".git").exists():
        print("ecosystem.git 不存在，跳过")
        return 1

    changed = False
    # 1) memories / skills / scripts
    for name in ("memories", "skills", "scripts"):
        if sync_tree(HERMES / name, REPO / name, SKIP_SKILLS if name == "skills" else None):
            changed = True

    # 2) designs/workflows 每日只读快照（保留历史）
    snap = REPO / "archive" / TODAY
    for name in ("designs", "workflows"):
        if (AGENT_INDEX / name).exists():
            if sync_tree(AGENT_INDEX / name, snap / name):
                changed = True

    # 3) protected.md/json（单文件复制，绝不用 sync_tree 扫源父目录）
    for f in ("protected.md", "protected.json"):
        src = HERMES / f
        if src.exists():
            dst = REPO / f
            try:
                if not dst.exists() or src.stat().st_mtime_ns != dst.stat().st_mtime_ns:
                    shutil.copy2(src, dst)
                    changed = True
            except OSError:
                shutil.copy2(src, dst)
                changed = True

    # 4) commit（无变化跳过）
    r = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.stdout.strip():
        subprocess.run(["git", "-C", str(REPO), "add", "-A"], check=True)
        msg = f"eco snapshot {TODAY}: {len(r.stdout.strip().splitlines())} changes"
        subprocess.run(["git", "-C", str(REPO), "commit", "-m", msg], check=True)
        print(f"✅ committed: {msg}")
    else:
        print(f"ℹ️ {TODAY} 无变化，跳过 commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
