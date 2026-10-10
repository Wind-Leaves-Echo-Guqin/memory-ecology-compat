#!/usr/bin/env python3
"""从生产语料生成 golden 候选对（供人工标注）。

语料：
  - memories/detail/*.md     正文（门①/④ 实际比较的对象）
  - experiences/*.md         title + symptom/cause/action（经验线）

候选对策略（覆盖三类）：
  1. 同 type 高相似度对（difflib/ngram 排序 top）——多为 same/similar，最难的边界
  2. 跨文件同主题（共享罕见 token）——same/similar
  3. 随机负样本对——distinct
  4. 超短/退化用例——边界

输出：JSON 候选（含各后端分数），供人工逐对打 label。
用法：
  python gen_golden_candidates.py --n 150 --out golden/_candidates.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "lib"))

HERMES = Path.home() / "AppData" / "Local" / "hermes"


def _fm(text: str) -> tuple[dict, str]:
    """极简 frontmatter 解析（宽松，兼容 CRLF）。"""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    fm_raw = text[3:end]
    body = text[end + 4:].lstrip("\n")
    meta = {}
    for line in fm_raw.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body


def load_detail(limit: int | None = None) -> list[dict]:
    d = HERMES / "memories" / "detail"
    out = []
    for p in sorted(d.glob("*.md")):
        meta, body = _fm(p.read_text(encoding="utf-8", errors="replace"))
        body = body.strip()
        if len(body) < 8:
            continue
        out.append({"slug": p.stem, "type": meta.get("type", "?"), "body": body})
    return out[:limit] if limit else out


def load_experiences(limit: int | None = None) -> list[dict]:
    d = HERMES / "experiences"
    out = []
    for p in sorted(d.glob("exp-*.md")):
        meta, body = _fm(p.read_text(encoding="utf-8", errors="replace"))
        m = re.search(r"^title:\s*(.+)$", body, re.M)
        title = m.group(1).strip() if m else ""
        if len(title) < 6:
            continue
        out.append({"slug": p.stem, "type": meta.get("type", "?"), "body": title})
    return out[:limit] if limit else out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150, help="目标候选对数")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "golden" / "_candidates.json")
    args = ap.parse_args()

    random.seed(20261004)
    detail = load_detail()
    exps = load_experiences()
    corpus = detail + exps
    print(f"语料：detail {len(detail)} + exp {len(exps)} = {len(corpus)}")

    # 惰性导入 similarity（避免依赖）
    from lib import similarity
    import os

    def sc(a, b, be):
        os.environ["MEMORY_ECOLOGY_SIM_BACKEND"] = be
        return round(similarity.ratio(a, b), 4)

    pairs = []
    seen = set()

    def add(a, b, strategy):
        key = tuple(sorted((a["slug"], b["slug"])))
        if key in seen or a["slug"] == b["slug"]:
            return
        seen.add(key)
        pairs.append({
            "strategy": strategy,
            "a_slug": a["slug"], "b_slug": b["slug"],
            "a_type": a["type"], "b_type": b["type"],
            "a": a["body"], "b": b["body"],
            "difflib": sc(a["body"], b["body"], "difflib"),
            "ngram": sc(a["body"], b["body"], "ngram"),
        })

    # 策略1：同 type 内 difflib top 相似对（最难的边界）
    by_type = {}
    for c in corpus:
        by_type.setdefault(c["type"], []).append(c)
    for t, group in by_type.items():
        if len(group) < 2:
            continue
        scored = []
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                s = sc(group[i]["body"], group[j]["body"], "difflib")
                if s >= 0.35:
                    scored.append((s, group[i], group[j]))
        scored.sort(key=lambda x: -x[0])
        for s, a, b in scored[:20]:
            add(a, b, f"same_type_top:{t}")

    # 策略2：跨语料共享罕见 token（同主题）
    def toks(s):
        return set(re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2}", s))
    idx = {}
    for c in corpus:
        for tk in toks(c["body"]):
            idx.setdefault(tk, []).append(c)
    rare = [(tk, v) for tk, v in idx.items() if 2 <= len(v) <= 4]
    random.shuffle(rare)
    for tk, v in rare[:60]:
        for i in range(len(v)):
            for j in range(i + 1, len(v)):
                add(v[i], v[j], f"shared_token:{tk}")

    # 策略3：随机负样本
    for _ in range(args.n * 2):
        if len(pairs) >= args.n:
            break
        a, b = random.sample(corpus, 2)
        add(a, b, "random")

    pairs = pairs[:args.n]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "note": "候选对（未标注）。人工在 label 字段填 same/similar/distinct。",
        "count": len(pairs),
        "pairs": pairs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写出 {len(pairs)} 对 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
