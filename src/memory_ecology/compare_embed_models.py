#!/usr/bin/env python3
"""多模型 × pooling 对比评测（2026-10-04）。

在 golden 集上对每个候选模型（× mean/cls pooling）扫描阈值，输出：
  - ROC 关键点：FPR=0 时可回收 same 数 / same_recall
  - 最优阈值（FPR=0 约束下 recall 最大；并列取更靠上的阈值）
  - F1 最优阈值（若允许少量 FPR）
  - 与 difflib/ngram 基线对比

用法：
  python compare_embed_models.py                 # 全部已下载模型
  python compare_embed_models.py --models bge-small-zh-v1.5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ME = HERE  # 本文件与 lib/ 同目录（src/memory_ecology）
sys.path.insert(0, str(ME))
sys.path.insert(0, str(ME / "lib"))

GOLDEN = HERE / "golden" / "similarity_golden.json"
def _hermes_models() -> Path:
    """模型根 = <生态根>/models（经 lib.config.hermes_root() 单源派生，跨树一致）。"""
    try:
        from lib.config import hermes_root
        return hermes_root() / "models"
    except Exception:
        return HERE.parent.parent / "models"


MODELS_ROOT = Path(os.environ.get("MEMORY_ECOLOGY_MODELS", _hermes_models()))


def eval_scores(pairs, score_fn, label=""):
    """返回 (scores, 指标)。scores = [(label, score)]。"""
    t0 = time.time()
    scores = [(p["label"], score_fn(p["a"], p["b"])) for p in pairs]
    dt = time.time() - t0
    same = sorted(s for lb, s in scores if lb == "same")
    distinct = sorted(s for lb, s in scores if lb == "distinct")
    similar = sorted(s for lb, s in scores if lb == "similar")

    # FPR=0 最优阈值：阈值 > max(distinct) 时 FPR=0，取能覆盖最多 same 的最低阈值
    dmax = max(distinct) if distinct else 1.0
    same_above = [s for s in same if s > dmax]
    t0_thr = min(same_above) if same_above else None
    recall0 = len(same_above) / max(len(same), 1)

    # F1 最优（扫描候选阈值 = 所有分数）
    best_f1, best_t = 0.0, None
    for t in sorted(set(same + distinct + similar)):
        tp = sum(1 for s in same if s >= t)
        fp = sum(1 for s in distinct if s >= t)
        fn = len(same) - tp
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        if f1 > best_f1:
            best_f1, best_t = f1, t

    return {
        "label": label,
        "n_same": len(same), "n_similar": len(similar), "n_distinct": len(distinct),
        "same_min": min(same) if same else 0, "same_med": same[len(same) // 2] if same else 0,
        "distinct_max": dmax, "distinct_med": distinct[len(distinct) // 2] if distinct else 0,
        "similar_min": min(similar) if similar else 0, "similar_max": max(similar) if similar else 0,
        "t_fpr0": t0_thr, "recall_fpr0": recall0, "recovered": len(same_above),
        "f1_best": best_f1, "t_f1": best_t,
        "secs": dt,
    }


def make_embed_fn(model_name, pooling):
    from lib.similarity import OnnxEmbedder
    os.environ["MEMORY_ECOLOGY_EMBED_POOLING"] = pooling
    emb = OnnxEmbedder(model_name)
    cache = {}

    def fn(a, b):
        for t in (a, b):
            if t not in cache:
                cache[t] = emb.embed(t)
        va, vb = cache[a], cache[b]
        return sum(x * y for x, y in zip(va, vb))
    return fn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=None)
    ap.add_argument("--golden", type=Path, default=GOLDEN)
    args = ap.parse_args()

    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    pairs = golden["pairs"]
    print(f"golden: {len(pairs)} 对（{golden.get('version')}）"
          f" {golden.get('counts', {})}\n")

    models = args.models or sorted(d.name for d in MODELS_ROOT.iterdir()
                                   if d.is_dir() and (d / "model.onnx").is_file())
    rows = []

    # 基线（字符级）
    from lib import similarity
    for be in ("difflib", "ngram"):
        os.environ["MEMORY_ECOLOGY_SIM_BACKEND"] = be
        rows.append(eval_scores(pairs, lambda a, b: similarity.ratio(a, b), be))
    os.environ["MEMORY_ECOLOGY_SIM_BACKEND"] = "embedding"

    for m in models:
        for pooling in ("mean", "cls"):
            try:
                fn = make_embed_fn(m, pooling)
                r = eval_scores(pairs, fn, f"{m}[{pooling}]")
                rows.append(r)
                print(f"  ✓ {m}[{pooling}]  {r['secs']:.1f}s")
            except Exception as e:
                print(f"  ✗ {m}[{pooling}]: {type(e).__name__}: {str(e)[:90]}")

    print(f"\n{'模型':<34}{'FPR=0阈值':>10}{'回收same':>9}{'recall':>8}"
          f"{'distinct_max':>13}{'same_med':>9}{'F1best':>8}{'t_F1':>7}")
    for r in rows:
        t = f"{r['t_fpr0']:.3f}" if r["t_fpr0"] is not None else "-"
        tf1 = f"{r['t_f1']:.3f}" if r["t_f1"] is not None else "-"
        print(f"{r['label']:<34}{t:>10}{r['recovered']:>6}/{r['n_same']:<3}"
              f"{r['recall_fpr0']:>7.1%}{r['distinct_max']:>13.3f}"
              f"{r['same_med']:>9.3f}{r['f1_best']:>8.3f}{tf1:>7}")

    out = HERE / "golden" / "_model_compare.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
