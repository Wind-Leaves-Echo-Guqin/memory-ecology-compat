#!/usr/bin/env python3
"""阈值迁移：把旧 difflib 阈值的"操作点"映射到新后端（语义不漂移）。

迁移原则（比"抄一个数字"正确）：每个门阈值在旧后端下有一个可测的操作点
（捕获率 / 误合并率），在新后端下找到操作点相同的阈值。这样换后端不改变门的
行为语义，只改变分数的刻度。

用法：python migrate_thresholds.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
ME = HERE  # 本文件与 lib/ 同目录（src/memory_ecology）
sys.path.insert(0, str(ME))
sys.path.insert(0, str(ME / "lib"))
GOLDEN = ME / "golden" / "similarity_golden.json"
def _hermes_models() -> Path:
    """模型根 = <生态根>/models（经 lib.config.hermes_root() 单源派生，跨树一致）。"""
    try:
        from lib.config import hermes_root
        return hermes_root() / "models"
    except Exception:
        return HERE.parent.parent / "models"


MODELS_ROOT = Path(os.environ.get("MEMORY_ECOLOGY_MODELS", _hermes_models()))

import difflib


def norm(s):
    return "".join(ch.lower() for ch in s if ch.isalnum())


GATES = [
    ("门① SIM_SUSPECT", 0.50, "capture_same_similar"),
    ("门① SIM_NEAR", 0.80, "fpr_sim_distinct"),
    ("门① NOOP", 0.95, "capture_near_identical"),
    ("门③ REPLACE_RATIO", 0.80, "fpr_sim_distinct"),
    ("门④ SIMILARITY_THRESHOLD", 0.70, "recall_same"),
]


def main() -> int:
    g = json.loads(GOLDEN.read_text(encoding="utf-8"))
    pairs = g["pairs"]
    for p in pairs:
        p["_d"] = difflib.SequenceMatcher(None, norm(p["a"]), norm(p["b"])).ratio()
    labels = [p["label"] for p in pairs]
    near_mask = [p["_d"] >= 0.95 for p in pairs]

    # 每个后端：一次性算全部 pair 分数（缓存 embedding）
    def score_difflib():
        from lib import similarity
        os.environ["MEMORY_ECOLOGY_SIM_BACKEND"] = "difflib"
        return [similarity.ratio(p["a"], p["b"]) for p in pairs]

    def score_ngram():
        from lib import similarity
        os.environ["MEMORY_ECOLOGY_SIM_BACKEND"] = "ngram"
        return [similarity.ratio(p["a"], p["b"]) for p in pairs]

    def score_embed(model, pooling):
        from lib.similarity import OnnxEmbedder
        os.environ["MEMORY_ECOLOGY_EMBED_POOLING"] = pooling
        emb = OnnxEmbedder(model)
        c = {}

        def e(t):
            if t not in c:
                c[t] = emb.embed(t)
            return c[t]
        return [sum(x * y for x, y in zip(e(p["a"]), e(p["b"]))) for p in pairs]

    backends = {"difflib": score_difflib(), "ngram": score_ngram()}
    for m in ("bge-small-zh-v1.5", "bge-base-zh-v1.5", "bge-large-zh-v1.5"):
        if (MODELS_ROOT / m / "model.onnx").is_file():
            backends[f"embed:{m}[cls]"] = score_embed(m, "cls")

    def op_point(scores, t, kind):
        if kind == "capture_same_similar":
            idx = [i for i, lb in enumerate(labels) if lb in ("same", "similar")]
        elif kind == "fpr_sim_distinct":
            idx = [i for i, lb in enumerate(labels) if lb in ("similar", "distinct")]
        elif kind == "capture_near_identical":
            idx = [i for i, n in enumerate(near_mask) if n]
        elif kind == "recall_same":
            idx = [i for i, lb in enumerate(labels) if lb == "same"]
        else:
            raise ValueError(kind)
        return sum(1 for i in idx if scores[i] >= t) / max(len(idx), 1)

    print(f"golden {len(pairs)}：same={labels.count('same')} similar={labels.count('similar')}"
          f" distinct={labels.count('distinct')} near={sum(near_mask)}\n")
    short = {k: k.split(":")[-1].replace("bge-", "").replace("-zh-v1.5", "")[:14]
             for k in backends}
    print(f"{'门/常量':<26}{'旧值':>6}{'操作点':>8}  " +
          "".join(f"{short[b]:>17}" for b in backends))
    for gname, old, kind in GATES:
        base = op_point(backends["difflib"], old, kind)
        # 捕获类（recall/capture）取"仍达标的最紧阈值"（最大 t，少误报）；
        # 误合并类（fpr）取"仍达标的最松阈值"（最小 t，多召回）。
        prefer_max = kind in ("capture_same_similar", "capture_near_identical", "recall_same")
        row = []
        for b, scores in backends.items():
            grid = sorted(set(scores))
            best_op = min(abs(op_point(scores, t, kind) - base) for t in grid)
            cands = [t for t in grid if abs(op_point(scores, t, kind) - base) <= best_op + 1e-9]
            best = max(cands) if prefer_max else min(cands)
            row.append((best, op_point(scores, best, kind)))
        print(f"{gname:<26}{old:>6.2f}{base:>8.0%}  " +
              "".join(f"{t:>10.2f}({o:.0%})" for t, o in row))

    print("\n说明：单元格 = 新后端下操作点等价的阈值(该阈值下实际操作点)。"
          "\n     difflib 列应≈旧值（校验）。门①④/③ 建议取 embed:bge-small 列。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
