#!/usr/bin/env python3
"""选型决策表：在"误合并预算"约束下的 same 召回（含 hard_same 分层）。

生产代价模型（门①③④ 都是"判合并/替换"）：
  - 误合并 similar/distinct = 丢事实（不可逆，最痛）
  - 漏合并 same = 多留一条近似重复（可容忍，检索层能兜）
故选型看：给定 similar+distinct 误合并率预算，same 召回（尤其 hard_same）谁高。

用法：python tradeoff_table.py
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


def build_fns():
    from lib import similarity
    from lib.similarity import OnnxEmbedder
    fns = {}

    def mk_char(be):
        # 闭包内显式设 env——避免 lambda 共享可变环境导致后端串味
        def fn(a, b):
            os.environ["MEMORY_ECOLOGY_SIM_BACKEND"] = be
            return similarity.ratio(a, b)
        return fn

    fns["difflib"] = mk_char("difflib")
    fns["ngram"] = mk_char("ngram")

    def mk(model, pooling):
        os.environ["MEMORY_ECOLOGY_EMBED_POOLING"] = pooling
        emb = OnnxEmbedder(model)
        cache = {}

        def fn(a, b):
            for t in (a, b):
                cache.setdefault(t, emb.embed(t))
            return sum(x * y for x, y in zip(cache[a], cache[b]))
        return fn

    for m in sorted(d.name for d in MODELS_ROOT.iterdir()
                    if d.is_dir() and (d / "model.onnx").is_file()):
        if "text2vec" in m:
            continue  # 已证退化（分数压缩到 0.98+）
        for pl in ("mean", "cls"):
            fns[f"{m}[{pl}]"] = mk(m, pl)
    return fns


def main() -> int:
    g = json.loads(GOLDEN.read_text(encoding="utf-8"))
    pairs = g["pairs"]
    for p in pairs:
        p["_d"] = difflib.SequenceMatcher(None, norm(p["a"]), norm(p["b"])).ratio()
    hard = [p for p in pairs if p["label"] == "same" and p["_d"] < 0.5]
    easy = [p for p in pairs if p["label"] == "same" and p["_d"] >= 0.5]
    sim = [p for p in pairs if p["label"] == "similar"]
    dis = [p for p in pairs if p["label"] == "distinct"]
    print(f"golden {len(pairs)}: same={len(hard)+len(easy)}(hard={len(hard)}) "
          f"similar={len(sim)} distinct={len(dis)}\n")

    fns = build_fns()
    budgets = [0.0, 0.03, 0.06, 0.10, 0.20]
    print(f"{'后端':<32}" + "".join(f"{'误合并≤'+f'{b:.0%}':>13}" for b in budgets))
    print(f"{'':<32}" + "".join(f"{'阈值/recall':>13}" for _ in budgets))
    for name, fn in fns.items():
        hs = [fn(p["a"], p["b"]) for p in hard]
        es = [fn(p["a"], p["b"]) for p in easy]
        sm = [fn(p["a"], p["b"]) for p in sim]
        ds = [fn(p["a"], p["b"]) for p in dis]
        cells = []
        for b in budgets:
            best = None
            for t in sorted(set(hs + es + sm + ds)):
                fp = (sum(1 for s in sm if s >= t) + sum(1 for s in ds if s >= t)) / (len(sm) + len(ds))
                if fp > b + 1e-9:
                    continue
                rec = (sum(1 for s in hs if s >= t) + sum(1 for s in es if s >= t)) / (len(hs) + len(es))
                if best is None or rec > best[0]:
                    best = (rec, t)
            cells.append(f"{best[1]:.2f}/{best[0]:.0%}" if best else "-")
        print(f"{name:<32}" + "".join(f"{c:>13}" for c in cells))

    print("\n注：单元格 = 最优阈值 / same 总召回（hard+easy 合并计）。"
          "\n    『误合并』= similar∪distinct 中被判 ≥ 阈值的比例（丢事实代价）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
