#!/usr/bin/env python3
"""VERSION.md 健康行生成（memory-skill-ecosystem 项目 3.6 健康双保险② + 3.1）。

VERSION.md = 版本 + 基因库当日 commit 状态 + cron 健康 + 每日健康行。
机制化启动检查的基础（会话启动注入读此文件）。

用法: python eco_version.py   （cron 每日 12:35，体检后）
"""
import datetime
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from lib.config import hermes_root
from lib.metrics import MEMORY_TRIGGER as MEM_DISPLAY_MAX, chars_of, parse_l1  # 口径单源（PORT_SPEC §4-C）

HERMES = hermes_root()
REPO = HERMES / "ecosystem.git"
OUT = HERMES / "VERSION.md"
ECOSYSTEM_VERSION = "v2.2.5"  # ⑧ 版本行常量（2026-10-04 统一：此前 README 双提 v2.2.4/v2.2.5、代码 v2.2.0 三方不一致；v3.0.0 仍待架构升级完成时切换并移居 lib）
JOBS = HERMES / "cron" / "jobs.json"
EXEC_DB = HERMES / "cron" / "executions.db"


def main() -> int:
    today = datetime.date.today().isoformat()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # 1) 基因库
    git_ok = git_commit = "?"
    try:
        r = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%h %ad %s",
                            "--date=short"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            git_commit = r.stdout.strip()
            # Q17 修复（2026-09-06）：原为首字符 ∈ 8-9a-f 的假检查（0-7 开头恒 ？，
            # 快照停摆也显示 ✓）——改为真检查：最新快照日期 == 今天才 ✓
            # （顺带删除原 r2/today_ok 死变量——算出后从未被健康行引用）
            last_date = git_commit.split(" ")[1] if " " in git_commit else ""
            git_ok = "✓" if last_date == today else "✗ 无当日提交"
    except Exception as e:
        git_ok = f"✗ {e}"

    # 2) cron 健康
    cron_err = 0
    cron_total = 0
    try:
        data = json.loads(JOBS.read_text(encoding="utf-8"))
        jobs = data.get("jobs", [])
        cron_total = len(jobs)
        cron_err = sum(1 for j in jobs if j.get("last_status") == "error")
    except Exception as e:
        cron_err = f"读取失败 {e}"

    # 3) 连续失败
    streak = 0
    try:
        conn = sqlite3.connect(f"file:{EXEC_DB}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT job_id, status FROM executions ORDER BY started_at DESC LIMIT 40")
        rows = cur.fetchall()
        conn.close()
        by_job = {}
        for jid, st in rows:
            by_job.setdefault(jid, []).append(st)
        streak = sum(1 for v in by_job.values() if len(v) >= 2 and all(s == "failed" for s in v[:2]))
    except Exception:
        pass

    line = f"| {today} | 基因库 {git_ok} ({git_commit[:60]}) | cron {cron_total - (cron_err if isinstance(cron_err, int) else 0)}/{cron_total} ok | 连续失败 {streak} | 记忆占用 {_mem_pct()} |"
    # ⑧ v2.1.1（2026-09-05）：版本行固化进生成器——此前版本行手写在 VERSION.md 被每日重建覆盖丢失
    # （9-03 加的「生态版本 v2.1.0」9-04 12:35 重建时被冲掉）。此后每日重建恒带版本行。
    lines = [f"# Hermes 生态 VERSION.md", "",
             f"**生态版本: {ECOSYSTEM_VERSION}**（Hermes 生产树 = 兼容版 = 开源树 同版；修复台账见桌面 T0 问题集 §6）", "",
             f"更新时间: {now}", "",
             f"## 每日健康行", "",
             f"| 日期 | 基因库 | cron | 连续失败 | 记忆 |", "|---|---|---|---|---|", line]

    prev = ""
    if OUT.exists():
        prev = OUT.read_text(encoding="utf-8", errors="replace")
        # 保留旧健康行（追加）。Q18 修复（2026-09-06）：原逻辑把旧行整体插到新表头之前
        # （孤儿行 + 双表并存 + 永不去重）——改为同日去重（保留最新）并入新表头之后
        if "| 20" in prev:
            seen: dict[str, str] = {}
            for l in prev.splitlines():
                if l.startswith("| 20") and not l.startswith(f"| {today} "):
                    seen[l.split("|")[1].strip()] = l
            if seen:
                # 审查 note 加固：分隔行缺失时不再 StopIteration（当前表结构恒在，防未来重构）
                sep = next((i for i, l in enumerate(lines) if l.startswith("|---")), None)
                if sep is not None:
                    lines = lines[:sep + 2] + list(seen.values()) + lines[sep + 2:]

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✅ VERSION.md 已更新: {line}")
    return 0


def _mem_pct() -> str:
    try:
        p = HERMES / "memories" / "MEMORY.md"
        # T0-Q3 修复（2026-09-05）：len() 本身即字符数，原 `n // 3` 把水位假报成 1/3
        # （2941 字符已超 2550 线却显示 980），超限监控永不触发。回归：test_eco_version.py
        # 口径单源（PORT_SPEC §4-C）：分子从 len(raw) 改为门②同口径（审查 note 6 补齐）
        n = chars_of(parse_l1(p.read_text(encoding="utf-8", errors="replace"))[0])
        return f"{n}字/~{MEM_DISPLAY_MAX}"
    except Exception:
        return "?"


if __name__ == "__main__":
    sys.exit(main())
