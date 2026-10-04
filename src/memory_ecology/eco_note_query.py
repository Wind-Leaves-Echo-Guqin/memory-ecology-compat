#!/usr/bin/env python3
"""经验笔记本 · 关键词检索入口（v1.1-8，2026-09-02 双审采纳）

只读 CLI：python eco_note_query.py <关键词> [--full] [--top N]

- 对 experiences/exp-*.md 的 frontmatter + 正文字段做归一化子串匹配（零分词依赖，
  中文直接子串，英文小写化——45 条规模无需向量库/分词器）
- 同 episode 折叠：同 source（session + 信号类型）仅保留 1 条，
  防「注入 3 条 = 同一事件 3 遍」（dsh 评审发现 45 条存在近重复对）
- 输出摘要行：id / type / status / trigger / evidence 首行（--full 时输出完整正文）

验收口径（dsh 评审）：报错文本命中率抽样，而非全库精确检索。

v2.2.5：加进程内解析缓存（(mtime,size) 失效）——错误检索按关键词逐个调本模块，
未缓存时同一批条目被重复解析 20 遍（真机实测 504ms/次），是 dsh 注入探针的主要开销。
"""
from __future__ import annotations

import argparse
import sys
import time
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


# v2.2.5：库快照缓存。eco_note_error_query.rank() 对**每个关键词**调一次 search()，
# 未缓存时 20 个关键词 × 237 条 ≈ 4700 次 文件读+解析+lower()（真机实测 504ms/次）；
# 库增长后线性放大，最终撞上 dsh 适配器的 spawn 8s 上限（超时即静默不注入）。
# 两级缓存：
#   _FILE_CACHE   单文件解析结果，键 = (st_mtime_ns, st_size)（任何写入都会改 mtime）
#   _LIB_SNAPSHOT 整库快照（条目 + 预算好的归一化 haystack），TTL 内不重复 stat/解析
# TTL 只影响长驻进程感知新条目的延迟；CLI 进程内首次调用后，同进程后续 search() 命中快照（error_query.rank() 逐关键词调用即受益）。
_FILE_CACHE: dict[str, tuple[int, int, dict | None]] = {}
_LIB_SNAPSHOT: tuple[tuple, float, list[tuple[Path, dict, str, str]]] | None = None
LIB_SNAPSHOT_TTL_S = 5.0


def _cached_entry(path: Path) -> dict | None:
    """解析 exp-*.md（带 (mtime,size) 失效的进程内缓存）。"""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size)
    hit = _FILE_CACHE.get(str(path))
    if hit is not None and (hit[0], hit[1]) == key:
        return hit[2]
    entry = _parse_entry(path)
    _FILE_CACHE[str(path)] = (key[0], key[1], entry)
    return entry


def _snapshot_key() -> tuple:
    """快照键：目录身份 + 目录 mtime（新增/删除条目会改目录 mtime）。"""
    try:
        st = EXP_DIR.stat()
    except OSError:
        return (str(EXP_DIR), 0)
    return (str(EXP_DIR), st.st_mtime_ns)


def _library() -> list[tuple[Path, dict, str, str]]:
    """整库快照 → [(path, entry, 归一化全文, episode 键)]。

    键含 EXP_DIR 身份（调用方会改 eq.EXP_DIR 指向别的数据根）与目录 mtime；
    TTL 到期后重建，重建时逐文件校验 (mtime,size) —— 长驻进程最多 5s 感知到
    已有条目的原地修改；CLI 进程内首次调用后，同进程后续 search() 命中快照（error_query.rank() 逐关键词调用即受益）。
    """
    global _LIB_SNAPSHOT
    now = time.monotonic()
    key = _snapshot_key()
    if _LIB_SNAPSHOT is not None:
        old_key, old_at, items = _LIB_SNAPSHOT
        if old_key == key and (now - old_at) < LIB_SNAPSHOT_TTL_S:
            return items
    items: list[tuple[Path, dict, str, str]] = []
    for path in sorted(EXP_DIR.glob("exp-*.md")):
        entry = _cached_entry(path)
        if entry is None:
            continue
        items.append((path, entry, _norm(entry["full"]), _episode_key(entry)))
    _LIB_SNAPSHOT = (key, now, items)
    return items


def clear_cache() -> None:
    """清空解析/快照缓存（测试与长驻进程显式失效用）。"""
    global _LIB_SNAPSHOT
    _FILE_CACHE.clear()
    _LIB_SNAPSHOT = None


def _norm(s: str) -> str:
    """归一化：小写 + 去首尾空白（中文直接保留子串匹配）。"""
    return s.strip().lower()


def _episode_key(entry: dict) -> str:
    """同 episode 折叠键：source（session + 信号类型）。"""
    return entry["meta"].get("source", "")


def search(keyword: str, top: int = 3, full: bool = False) -> list[dict]:
    kw = _norm(keyword)
    hits: list[dict] = []
    for path, entry, haystack, episode in _library():
        if kw not in haystack:
            continue
        hits.append({"path": path.stem, "entry": entry, "episode": episode})
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
