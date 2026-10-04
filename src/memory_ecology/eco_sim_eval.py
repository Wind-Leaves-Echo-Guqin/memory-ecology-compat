#!/usr/bin/env python3
"""相似度 golden set 评测（批 5，2026-10-04）——换内核的标尺。

用法:
  python eco_sim_eval.py                     # 全后端 × 全阈值表
  python eco_sim_eval.py --backend ngram     # 单后端
  python eco_sim_eval.py --threshold 0.7     # 指定阈值看混淆明细

口径:
  golden.pairs 为人工标注对（label: same=同一事实可合并 / similar=相关但不同条 /
  distinct=无关）。指标：
    same_recall        same 对中 ratio ≥ 阈值 的比例（漏合并代价）
    distinct_fpr       distinct 对中 ratio ≥ 阈值 的比例（误合并代价——更致命）
    similar_boundary   similar 对的得分区间（应落在阈值附近，作校准带）
  换后端判定：distinct_fpr 不升高的前提下 same_recall 更高者胜。

数据来源与扩充路线（ARCHITECTURE_TODO §相似度）：
  种子集 v0 为代表性合成对（本文件）；正式集应从 detail 现库 + gate_log 历史
  + dsh 评审 45 条近重复对人工标注扩充至 80-120 对（P0.5 月度仪式顺带积累）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib import similarity

GOLDEN = Path(__file__).parent / "golden" / "similarity_golden.json"

DEFAULT_THRESHOLD = 0.80
BACKENDS = ["difflib", "ngram"]  # embedding 有依赖时自动追加


def main() -> int:
    ap = argparse.ArgumentParser(description="相似度后端 golden set 评测")
    ap.add_argument("--backend", choices=BACKENDS + ["embedding", "all"], default="all")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--golden", type=Path, default=GOLDEN)
    args = ap.parse_args()

    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    pairs = golden["pairs"]
    backends = BACKENDS + (["embedding"] if args.backend in ("embedding", "all") else []) \
        if args.backend == "all" else [args.backend]

    print(f"golden: {len(pairs)} 对（{golden.get('version', '?')}），阈值 {args.threshold}")

    def best_threshold(scores):
        """FPR=0 约束下使 same_recall 最大的阈值（distinct 最高分之上取 same 最低分）。"""
        dmax = max((s for lb, s in scores if lb == "distinct"), default=1.0)
        same_ok = [s for lb, s in scores if lb == "same" and s > dmax]
        return (min(same_ok), len(same_ok)) if same_ok else (None, 0)

    print(f"{'backend':<10} {'recall@T':>9} {'fpr@T':>7} {'similar带':>16} {'FPR=0最优阈值':>14} {'可回收same数':>10}")
    for be in backends:
        env_save = dict(os.environ)
        os.environ["MEMORY_ECOLOGY_SIM_BACKEND"] = be
        try:
            scores = [(p["label"], similarity.ratio(p["a"], p["b"])) for p in pairs]
        finally:
            os.environ.clear()
            os.environ.update(env_save)
        same = [s for lb, s in scores if lb == "same"]
        distinct = [s for lb, s in scores if lb == "distinct"]
        similar = [s for lb, s in scores if lb == "similar"]
        recall = sum(1 for s in same if s >= args.threshold) / max(len(same), 1)
        fpr = sum(1 for s in distinct if s >= args.threshold) / max(len(distinct), 1)
        band = f"{min(similar):.2f}~{max(similar):.2f}" if similar else "-"
        t, recovered = best_threshold(scores)
        t_str = f"{t:.2f}" if t is not None else "无（distinct 分数高于部分 same）"
        print(f"{be:<10} {recall:>9.2%} {fpr:>7.2%} {band:>16} {t_str:>14} {recovered:>6}/{len(same)}")

    if args.backend != "all":
        be = args.backend
        os.environ["MEMORY_ECOLOGY_SIM_BACKEND"] = be
        for p in pairs:
            s = similarity.ratio(p["a"], p["b"])
            flag = "HIT " if s >= args.threshold else "miss"
            print(f"  [{flag}] {s:.3f} {p['label']:<8} {p['a'][:24]} | {p['b'][:24]}")
    return 0


if __name__ == "__main__":
    import os
    sys.exit(main())
