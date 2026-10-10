#!/usr/bin/env python3
"""把 _candidates.json 的人工标注 + 随机负样本 + 种子集合并为正式 golden。

标注口径（与 eco_sim_eval.py 一致）：
  same     = 同一事实，可合并（门① UPDATE/NOOP、门④ merge 的正例）
  similar  = 相关但应保持独立条（边界带，不应触发合并）
  distinct = 无关（误合并代价的负例）

用法：python build_golden.py            # 写 compat + dev 两份
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
COMPAT_GOLDEN = HERE / "golden" / "similarity_golden.json"
DEV_GOLDEN = Path.home() / "memory-ecology-dev" / "scripts" / "golden" / "similarity_golden.json"

# 人工标注：_candidates.json 的 pairs 下标 -> label（2026-10-04 逐对人工判读 + 二轮复核）
# 判读口径：same=同一事实（措辞/粒度差异不改变事实，合并无损）；similar=同项目/同主题但
# 承载不同事实（合并会丢信息，必须保持独立条）；distinct=无关（仅共享通用词）。
LABELS = {
    # ── same（同一事实可合并）──
    **{i: "same" for i in [
        3, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18, 20, 21, 22, 23,
        25, 26, 27, 28, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42,
        43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 62, 63,
        65, 68, 70, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 83, 84, 85, 86,
        89, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 103, 104, 105, 106,
        107, 109, 110, 111, 112, 116, 117, 119, 122, 124, 131, 134, 140, 145,
    ]},
    # ── similar（相关但独立条：同项目不同事实 / 同主题不同粒度）──
    **{i: "similar" for i in [
        0, 1, 2, 4, 19, 24, 29, 58, 59, 60, 61, 64, 66, 67, 69, 71, 82, 87,
        88, 90, 91, 102, 108, 128, 129, 130, 142, 143, 144, 146, 147, 149,
    ]},
    # ── distinct（无关）──
    **{i: "distinct" for i in [
        11, 113, 114, 115, 118, 120, 121, 123, 125, 126, 127, 132, 133, 135,
        136, 137, 138, 139, 141, 148,
    ]},
}

HERMES = Path.home() / "AppData" / "Local" / "hermes"


def _fm(text: str):
    text = text.replace("\r\n", "\n")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[end + 4:].lstrip("\n")


def load_corpus():
    out = []
    for p in sorted((HERMES / "memories" / "detail").glob("*.md")):
        _, body = _fm(p.read_text(encoding="utf-8", errors="replace"))
        if len(body.strip()) >= 8:
            out.append({"slug": p.stem, "body": body.strip()})
    for p in sorted((HERMES / "experiences").glob("exp-*.md")):
        _, body = _fm(p.read_text(encoding="utf-8", errors="replace"))
        m = re.search(r"^title:\s*(.+)$", body, re.M)
        if m and len(m.group(1).strip()) >= 6:
            out.append({"slug": p.stem, "body": m.group(1).strip()})
    return out


def main() -> int:
    cands = json.loads((HERE / "golden" / "_candidates.json").read_text(encoding="utf-8"))["pairs"]
    pairs = []

    # 1) 人工标注的候选对
    for i, p in enumerate(cands):
        lb = LABELS.get(i)
        if lb is None:
            print(f"⚠️ 未标注下标 {i}，跳过")
            continue
        pairs.append({"a": p["a"], "b": p["b"], "label": lb,
                      "src": p["strategy"], "a_slug": p["a_slug"], "b_slug": p["b_slug"]})

    # 2) 随机负样本（补足 distinct，FPR 估计需要足够负例）
    #    守卫：语料高度同质（同项目条目多），随机对可能实为 similar——用 difflib 上限剔除
    sys.path.insert(0, str(HERE))
    import difflib
    import os
    os.environ["MEMORY_ECOLOGY_SIM_BACKEND"] = "difflib"
    from lib import similarity

    random.seed(20261004)
    corpus = load_corpus()
    have = {tuple(sorted((p["a"], p["b"]))) for p in pairs}
    added = 0
    while added < 30:
        a, b = random.sample(corpus, 2)
        key = tuple(sorted((a["body"], b["body"])))
        if key in have:
            continue
        if similarity.ratio(a["body"], b["body"]) > 0.30:  # 疑似同主题，剔除
            continue
        have.add(key)
        pairs.append({"a": a["body"], "b": b["body"], "label": "distinct",
                      "src": "random_neg", "a_slug": a["slug"], "b_slug": b["slug"]})
        added += 1

    # 3) 种子集（保留超短串钉死用例，防退化回归）
    seed = json.loads(COMPAT_GOLDEN.read_text(encoding="utf-8"))
    for p in seed["pairs"]:
        if p.get("note", "").startswith("超短串"):
            pairs.append({"a": p["a"], "b": p["b"], "label": p["label"],
                          "src": "seed_edge", "note": p["note"]})

    from collections import Counter
    dist = Counter(p["label"] for p in pairs)
    out = {
        "version": "v1-2026-10-04",
        "note": ("相似度 golden 集 v1：150 对生产语料人工标注（detail 84 条 + experiences 237 条，"
                 "策略=同 type 高相似 top / 共享罕见 token / 随机负样本）+ 30 对随机负样本 + 2 对超短串钉死。"
                 "label: same=同一事实可合并 / similar=相关但应保持独立条 / distinct=无关。"
                 "换后端判定：distinct_fpr 不升高的前提下 same_recall 更高者胜。"),
        "counts": dict(dist),
        "pairs": pairs,
        "queries": seed.get("queries", []),
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    for dest in (COMPAT_GOLDEN, DEV_GOLDEN):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        print(f"写出 {len(pairs)} 对 {dict(dist)} → {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
