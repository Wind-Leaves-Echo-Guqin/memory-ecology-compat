#!/usr/bin/env python3
"""检索质量报告 eco_retrieval_report（v2.3.0 S4，只读零依赖）。

数据源：memories/.memory_hits.jsonl（memory_query 每次查询落一行，**零结果也落行**）。
聚合：按日统计总查询数、零结果数/率、平均命中数；全局零结果查询 Top N——
零结果率是检索质量的头号在线诊断信号（区分「库里真没有 / 词面召回失败 / 关键词没选对」），
也是 difflib→ngram→embedding 默认化决策的在线证据链（OpenViking observer 同思想）。

定位：只读观测，不写任何文件、不进四道门；GUI「检索质量」卡片经 runner 调本脚本取数。

用法:
  python eco_retrieval_report.py [--days N] [--top N] [--format text|json]
返回码: 恒 0（观测工具，无数据也算正常输出）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections import defaultdict
from pathlib import Path

from lib.config import hermes_root

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERMES = hermes_root()
HITS_LOG = HERMES / "memories" / ".memory_hits.jsonl"


def load_records(path: Path) -> list[dict]:
    """读遥测 JSONL；坏行跳过（遥测允许损坏，报告不阻塞）。"""
    if not path.exists():
        return []
    out = []
    try:
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and "ts" in rec:
                out.append(rec)
    except OSError:
        return []
    return out


def aggregate(records: list[dict], days: int, top: int) -> dict:
    """按日聚合 + 全局零结果查询 Top N。days=0 表示不限窗口。"""
    today = datetime.date.today()
    cutoff = (today - datetime.timedelta(days=days - 1)).isoformat() if days > 0 else ""
    by_day: dict[str, dict] = defaultdict(lambda: {"total": 0, "zero": 0, "hits": 0})
    zero_queries: dict[str, int] = defaultdict(int)
    for rec in records:
        day = str(rec.get("ts", ""))[:10]
        if not day or (cutoff and day < cutoff):
            continue
        hits = rec.get("hits")
        n = len(hits) if isinstance(hits, list) else 0
        d = by_day[day]
        d["total"] += 1
        d["hits"] += n
        if n == 0:
            d["zero"] += 1
            q = str(rec.get("query", "")).strip()
            if q:
                zero_queries[q] += 1  # 首尾去空格后分组，避免同词多写法碎片化 Top N
    total = sum(d["total"] for d in by_day.values())
    zero = sum(d["zero"] for d in by_day.values())
    return {
        "total_queries": total,
        "zero_queries": zero,
        "zero_rate": round(zero / total, 4) if total else 0.0,
        "by_day": [
            {"date": day, "total": d["total"], "zero": d["zero"],
             "zero_rate": round(d["zero"] / d["total"], 4) if d["total"] else 0.0,
             "avg_hits": round(d["hits"] / d["total"], 2) if d["total"] else 0.0}
            for day, d in sorted(by_day.items())
        ],
        "top_zero_queries": [{"query": q, "count": c}
                             for q, c in sorted(zero_queries.items(),
                                                key=lambda kv: (-kv[1], kv[0]))[:top]],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="检索质量报告（.memory_hits.jsonl 聚合，只读）")
    ap.add_argument("--days", type=int, default=30, help="统计窗口天数（默认 30，0=全部）")
    ap.add_argument("--top", type=int, default=5, help="零结果查询 Top N（默认 5）")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    records = load_records(HITS_LOG)
    agg = aggregate(records, args.days, args.top)
    agg["source"] = str(HITS_LOG)
    agg["window_days"] = args.days

    if args.format == "json":
        print(json.dumps(agg, ensure_ascii=False, indent=2))
        return 0

    if not agg["total_queries"]:
        print(f"暂无检索遥测数据（{HITS_LOG} 不存在或为空；memory_query 每次查询都会落行，"
              f"零结果也落）")
        return 0
    print(f"检索质量（近 {args.days or '∞'} 天）：{agg['total_queries']} 次查询，"
          f"零结果 {agg['zero_queries']} 次（零结果率 {agg['zero_rate']:.1%}）")
    print("按日（date | total | zero | rate | avg_hits）:")
    for d in agg["by_day"][-14:]:
        print(f"  {d['date']} | {d['total']:>4} | {d['zero']:>3} | {d['zero_rate']:>6.1%} | {d['avg_hits']}")
    if agg["top_zero_queries"]:
        print("零结果查询 Top（候选：换关键词 / 语义层缺口 / 库里真没有）:")
        for z in agg["top_zero_queries"]:
            print(f"  {z['count']:>3} × {z['query']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
