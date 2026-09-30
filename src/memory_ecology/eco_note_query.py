#!/usr/bin/env python3
"""经验笔记本 · 关键词检索入口（v1.1-8，2026-09-02 双审采纳）

只读 CLI：python eco_note_query.py <关键词> [--full] [--top N]

- 对 experiences/exp-*.md 的 frontmatter + 正文字段做归一化子串匹配（零分词依赖，
  中文直接子串，英文小写化——45 条规模无需向量库/分词器）
- 同 episode 折叠：同 source（session + 信号类型）仅保留 1 条，
  防「注入 3 条 = 同一事件 3 遍」（dsh 评审发现 45 条存在近重复对）
- 输出摘要行：id / type / status / trigger / evidence 首行（--full 时输出完整正文）

验收口径（dsh 评审）：报错文本命中率抽样，而非全库精确检索。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lib.config import hermes_root
from lib.memstore import parse_experience  # §C：解析单源

try:  # v0.3：子进程/管道下 stdout 走 GBK 会导致中文乱码，显式固定 UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERMES = hermes_root()
EXP_DIR = HERMES / "experiences"


def _parse_entry(path: Path) -> dict | None:
    """解析 exp-*.md：frontmatter（--- 块，key: value）+ 正文（title:/symptom:/...）。

    解析单源 memstore.parse_experience（原本地实现已逐字上收，零行为差异）。"""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_experience(raw)


def _norm(s: str) -> str:
    """归一化：小写 + 去首尾空白（中文直接保留子串匹配）。"""
    return s.strip().lower()


def _episode_key(entry: dict) -> str:
    """同 episode 折叠键：source（session + 信号类型）。"""
    return entry["meta"].get("source", "")


def search(keyword: str, top: int = 3, full: bool = False) -> list[dict]:
    kw = _norm(keyword)
    hits: list[dict] = []
    for path in sorted(EXP_DIR.glob("exp-*.md")):
        entry = _parse_entry(path)
        if entry is None:
            continue
        haystack = _norm(entry["full"])
        if kw not in haystack:
            continue
        hits.append({"path": path.stem, "entry": entry, "episode": _episode_key(entry)})
    # 同 episode 折叠：保留每个 episode 的第一条（按文件名字典序，即 id 序）
    seen: set[str] = set()
    folded: list[dict] = []
    for h in hits:
        if h["episode"] in seen:
            continue
        seen.add(h["episode"])
        folded.append(h)
    return folded[:top]


def _summary(h: dict) -> str:
    meta = h["entry"]["meta"]
    trig = h["entry"]["fields"].get("title", "")[:40]
    ev = h["entry"]["fields"].get("evidence", "")[:60]
    return (f"{h['path']} | {meta.get('type', '?')} | {meta.get('status', '?')} "
            f"| {trig} | 证:{ev}")


def main() -> int:
    ap = argparse.ArgumentParser(description="经验笔记本关键词检索（只读）")
    ap.add_argument("keyword", help="检索关键词（子串匹配）")
    ap.add_argument("--full", action="store_true", help="输出完整正文")
    ap.add_argument("--top", type=int, default=3, help="最多返回条数（默认 3，同 episode 折叠）")
    ap.add_argument("--format", choices=["text", "json"], default="text", help="输出格式（PORT_SPEC §4-C 类型化契约）")
    args = ap.parse_args()
    results = search(args.keyword, top=args.top, full=args.full)
    if args.format == "json":
        import json
        items = []
        for h in results:
            m = h["entry"]["meta"]
            items.append({
                "id": h["path"], "type": m.get("type", "?"), "status": m.get("status", "?"),
                "title": h["entry"]["fields"].get("title", ""), "evidence": h["entry"]["fields"].get("evidence", ""),
                **({"full": h["entry"]["full"]} if args.full else {}),
            })
        print(json.dumps({"ok": True, "query": args.keyword, "total": len(items), "hits": items}, ensure_ascii=False, indent=2))
        return 0
    if not results:
        print(f"未命中：{args.keyword}")
        return 1
    print(f"命中 {len(results)} 条（同 episode 已折叠）:")
    for h in results:
        print(f"  - {_summary(h)}")
        if args.full:
            print("    " + h["entry"]["full"].replace("\n", "\n    ").rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
