#!/usr/bin/env python3
"""经验笔记本 · INDEX.md 生成器（正式版 v1.0 组件）

扫描 experiences/ 目录条目（<id>.md），生成 INDEX.md 摘要表。
索引字段：id / type / status / trigger（标题）/ last_hit / 有无证据。
由 cron 每日重建（同 eco.db 模式）——维护负担=零的前提。

用法: python eco_note_index.py [--dir <experiences路径>]
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

from lib.config import hermes_root

HERMES = hermes_root()
EXP_DIR = HERMES / "experiences"

GROUP_ORDER = ("error", "negative", "success", "link", "pattern")


def parse_entry(path: Path) -> dict | None:
    """解析单条目文件的 frontmatter + trigger。非法文件返回 None（不拖垮全表）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    fm = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            kv = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
            if kv:
                fm[kv.group(1)] = kv.group(2).strip()
    bm = re.search(r"^title:\s*(.+)$", text, re.M) or re.search(r"^trigger:\s*(.+)$", text, re.M)
    title = (bm.group(1).strip().strip("\"'") if bm else "") or path.stem
    # 证据可能在前置字段（frontmatter 或正文行），两处都查（dsh 评审：frontmatter 查不到
    # 而 evidence 在正文 → 全表恒 "—" 的 bug）
    evidence = fm.get("evidence", "")
    if not evidence and re.search(r"^evidence:\s*\S", text, re.M):
        evidence = "有"
    return {
        "id": fm.get("id", path.stem),
        "type": fm.get("type", "?"),
        "status": fm.get("status", "?"),
        "title": title[:60],
        "last_hit": fm.get("last_hit", "")[:10],
        "evidence": evidence,
    }


def build_index(exp_dir: Path) -> str:
    lines = ["# 经验索引（自动生成，勿手改）", "",
             "| id | type | status | trigger | last_hit | 证据 |",
             "|---|---|---|---|---|---|"]
    entries = []
    for p in sorted(exp_dir.glob("exp-*.md")):
        e = parse_entry(p)
        if e:
            entries.append(e)
    for e in entries:
        lines.append(
            f"| {e['id']} | {e['type']} | {e['status']} | {e['title']} | {e['last_hit'] or '—'} | "
            f"{'✓' if e['evidence'] else '—'} |"
        )
    if not entries:
        lines.append("| （暂无条目） | | | | | |")
    lines += [
        "",
        f"生成时间: {datetime.now().isoformat(timespec='seconds')} | 条目数: {len(entries)}",
    ]
    return "\n".join(lines)


def main() -> int:
    exp = Path(sys.argv[sys.argv.index("--dir") + 1]) if "--dir" in sys.argv else EXP_DIR
    out = exp / "INDEX.md"
    txt = build_index(exp)
    # 原子写（tmp + replace），防止并发读半写
    tmp = out.with_suffix(".md.tmp")
    tmp.write_text(txt, encoding="utf-8")
    tmp.replace(out)
    print(f"✅ INDEX.md 已重建（{out}，{out.stat().st_size} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
