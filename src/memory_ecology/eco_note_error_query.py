#!/usr/bin/env python3
"""经验笔记本 · N4 报错检索接入（v1.1-1a，2026-09-02 双审采纳）

CLI：python eco_note_error_query.py "<报错文本>" [--top N] [--full]

- 规则关键词提取（无 LLM、零依赖）：英文 token（>=3 字符、去通用停用词）
  + 文件名/模块样式 token（含 .py/.sh/.exe/.md 后缀或大写/驼峰）
- 逐 token 走 eco_note_query.search（同 episode 折叠），按命中条目数聚合排序
- 渐进披露：默认摘要行（id/type/status/trigger/证据首行），--full 出完整正文
- 验收口径（dsh 评审）：真实 tool_error 文本命中率抽样

备注：1b（错误发生时自动注入 agent 上下文）与 #2（hooks 事件流）合成一个
hooks 程序先做可行性切片再立项——本文件只兑现「检索可用」，不碰 Hermes 本体。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter

import eco_note_query as eq

try:  # v0.3：子进程/管道下 stdout 走 GBK 会导致中文乱码，显式固定 UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "was", "were", "are",
    "is", "not", "but", "you", "your", "have", "has", "had", "cannot", "can",
    "could", "would", "should", "will", "shall", "may", "might", "must",
    "error", "errors", "exception", "exceptions", "traceback", "line", "lines",
    "file", "files", "path", "paths", "command", "commands", "failed", "failure",
    "fail", "success", "successful", "return", "returned", "status", "code",
    "token", "tokens", "version", "versions", "value", "values", "string",
    "type", "types", "message", "messages", "output", "result", "results",
    "none", "null", "true", "false", "unknown", "missing", "invalid", "expected",
    "got", "given", "number", "count", "warning", "warnings", "stderr", "stdout",
}


def extract_keywords(text: str, limit: int = 20) -> list[str]:
    """规则关键词提取：英文 token 过滤停用词；保留文件名/模块样式 token。"""
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_.]{2,}", text)
    out: list[str] = []
    seen: set[str] = set()
    for t in toks:
        low = t.lower()
        if low in STOPWORDS:
            continue
        # 纯数字 token 跳过
        if re.fullmatch(r"[\d_]+", t):
            continue
        # 短 token 仅在带文件后缀/大写/下划线时保留
        if (
            len(t) < 4
            and not re.search(r"\.(py|sh|exe|md|json|yaml|yml|txt|nc|log)$", t)
            and not re.search(r"[A-Z]", t)
            and "_" not in t
        ):
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out


# ── 根因维度（兼容版新增，T0 问题集 Q2 的修复；生产树未动）──────────────────
# Q2 病灶：按词匹配让「NameError（逻辑粗心）」命中「缺库」类经验（仅因都含
# python/报错等词）。修复：提取异常类名做根因分层——同根因 > 泛化 > 异根因沉底。
EXC_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning)|KeyboardInterrupt|SystemExit)\b"
)


def extract_exceptions(text: str) -> set[str]:
    """提取异常类名（NameError / ModuleNotFoundError / ValueError …）。"""
    return set(EXC_RE.findall(text or ""))


def _root_cause_tier(query_exc: set[str], entry_text: str) -> int:
    """根因分层：0=同根因（异常名相交）1=泛化（条目未点名异常）2=异根因（点名了不同异常）。"""
    if not query_exc:
        return 1
    entry_exc = extract_exceptions(entry_text)
    if entry_exc & query_exc:
        return 0
    return 2 if entry_exc else 1


def rank(text: str, top: int = 3) -> list[dict]:
    """逐关键词检索聚合：score = 该条目被多少关键词命中（多个关键词命中同一条目 → 更相关）。"""
    kws = extract_keywords(text)
    q_exc = extract_exceptions(text)
    per_entry: Counter[str] = Counter()
    entry_map: dict[str, dict] = {}
    for kw in kws:
        for h in eq.search(kw, top=20):
            entry_map.setdefault(h["path"], h)
            per_entry[h["path"]] += 1
    if q_exc:
        # 根因分层排序：tier 优先，同级按关键词命中数（Q2 修复：异根因不再靠词面词混淆上来）
        ranked = sorted(
            entry_map.values(),
            key=lambda h: (
                _root_cause_tier(q_exc, h["entry"]["full"]),
                -per_entry[h["path"]],
            ),
        )
    else:
        ranked = sorted(entry_map.values(), key=lambda h: -per_entry[h["path"]])
    # v2.1.1 ⑤（2026-09-04）：聚合后按 episode（source）二次折叠——
    # 防「注入 3 条 = 同一事件 3 遍」（dsh 预警 + 9-03 真实注入实测：不同 token 命中同 episode 不同条目）
    folded: list[dict] = []
    episode_seen: set[str] = set()
    for h in ranked:
        ep = h["entry"]["meta"].get("source", "")
        if ep and ep in episode_seen:
            continue
        if ep:
            episode_seen.add(ep)
        folded.append(h)
    return folded[:top]


def build_json(text: str, top: int) -> dict:
    """JSON 载荷（dsh 适配器消费）：关键词/异常名/命中摘要。"""
    hits = rank(text, top=top)
    return {
        "query_exc": sorted(extract_exceptions(text)),
        "keywords": extract_keywords(text)[:10],
        "n_hits": len(hits),
        "hits": [
            {
                "id": h["path"],
                "type": h["entry"]["meta"].get("type", "?"),
                "status": h["entry"]["meta"].get("status", "?"),
                "trigger": h["entry"]["fields"].get("title", "")[:80],
                "evidence": h["entry"]["fields"].get("evidence", "")[:120],
                "episode": h["episode"],
            }
            for h in hits
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="报错文本 → 经验检索（只读）")
    ap.add_argument("text", help="报错/异常文本")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()
    if args.format == "json":
        print(json.dumps(build_json(args.text, args.top), ensure_ascii=False))
        return 0
    kws = extract_keywords(args.text)
    if not kws:
        print("未能从文本提取关键词（太短或无有效 token）")
        return 2
    hits = rank(args.text, top=args.top)
    print(f"提取关键词 {len(kws)} 个: {', '.join(kws[:10])}{'…' if len(kws) > 10 else ''}")
    if not hits:
        print("无命中（经验库中暂无相关条目）")
        return 1
    print(f"命中 {len(hits)} 条:")
    for h in hits:
        print("  - " + eq._summary(h))
        if args.full:
            print("    " + h["entry"]["full"].replace("\n", "\n    ").rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
