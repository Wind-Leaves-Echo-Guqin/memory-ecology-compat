#!/usr/bin/env python3
"""经验笔记本 · 近重复合并清单生成器（v2.1.1 ⑥，2026-09-05）

只读：扫描正式层 101 条 + 回扫候选池（backfill/*.md），输出近似对清单（n-gram
Jaccard ≥ 0.75），附每对合并建议（保留信息更全/更新者，另一条 absorbed_into 留痕）。
**只生成清单，绝不自动落标**（cc 裁决：内容级合并误合有代价，人工勾选后才执行）。

用法: python eco_note_merge_list.py [--min-sim 0.75] [--out <路径>]
输出: experiences/pending/backfill/.merge-candidates-<ts>.md（默认）
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import eco_note_query as qy
import eco_note_backfill as bf

SIM_THRESHOLD = 0.75


def _norm(s: str) -> str:
    return re.sub(r"[\s\W]+", "", s.lower())


def sim(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    ga = {na[i:i + 2] for i in range(len(na) - 1)}
    gb = {nb[i:i + 2] for i in range(len(nb) - 1)}
    return len(ga & gb) / max(1, len(ga | gb))


def collect_items() -> list[dict]:
    """收集全部条目正文（正式层 + 候选池），零写入。"""
    items: list[dict] = []
    for p in qy.EXP_DIR.glob("exp-*.md"):
        e = qy._parse_entry(p)
        if e:
            items.append({"id": p.stem, "title": e["fields"].get("title", ""), "zone": "正式层"})
    for f in bf.backfill_dir().glob("*.md"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^-\s*\[(\w+)\]\s*\*\*(.+?)\*\*", line)
            if m:
                items.append({"id": m.group(2)[:40], "title": m.group(2), "zone": f"候选({f.name[:20]})"})
    return items


def find_pairs(items: list[dict], threshold: float) -> list[dict]:
    """近似对（贪心去重：每对只列一次，同 id 重复的 title 只比对一次）。"""
    pairs = []
    seen = set()
    for i in range(len(items)):
        a = items[i]
        if a["id"] in seen:
            continue
        for j in range(i + 1, len(items)):
            b = items[j]
            if b["id"] in seen:
                continue
            s = sim(a["title"], b["title"])
            if s >= threshold:
                keep = a if len(a["title"]) >= len(b["title"]) else b  # 建议保留标题更长者
                absorbed = b if keep is a else a
                pairs.append({"sim": round(s, 2), "keep": keep, "absorb": absorbed})
                seen.add(absorbed["id"])
                break
    return sorted(pairs, key=lambda x: -x["sim"])


def main() -> int:
    ap = argparse.ArgumentParser(description="近重复合并清单生成器（只读）")
    ap.add_argument("--min-sim", type=float, default=SIM_THRESHOLD)
    ap.add_argument("--out", default=None, help="输出清单路径（默认 backfill/.merge-candidates-<ts>.md）")
    args = ap.parse_args()
    items = collect_items()
    print(f"扫描 {len(items)} 条（正式层+候选池）")
    pairs = find_pairs(items, args.min_sim)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) if args.out else bf.backfill_dir() / f".merge-candidates-{ts}.md"
    lines = [f"# 近重复合并清单（v2.1.1 ⑥，{ts}，只读生成）", "",
             f"阈值 ≥{args.min_sim}；**共 {len(pairs)} 对**；动作：人工勾选后执行合并（保留 keep，吸收 absorb→absorbed_into）", ""]
    for i, p in enumerate(pairs, 1):
        lines.append(f"### 对 {i}（相似度 {p['sim']}）")
        lines.append(f"- 保留: [{p['keep']['zone']}] {p['keep']['id']} — {p['keep']['title'][:60]}")
        lines.append(f"- 吸收: [{p['absorb']['zone']}] {p['absorb']['id']} — {p['absorb']['title'][:60]}")
        lines.append(f"- 勾选: [ ] 合并此对（勾选后执行 eco_note_merge.py）")
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"清单 {len(pairs)} 对 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
