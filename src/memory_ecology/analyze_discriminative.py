#!/usr/bin/env python3
"""深度分析：难例召回 + similar 带误合并（真正的判别力）。

golden 的 distinct 多为易负例（difflib 中位 0.085），FPR=0 对谁都容易。
真正的判别力在：
  - hard_same：difflib<0.5 的 same 对（语义改写，字符级必漏）
  - similar_fp：similar 对被判 ≥ 阈值的比例（相关但独立条被误合并——生产最痛的代价）
用法：python analyze_discriminative.py
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


def main() -> int:
    g = json.loads(GOLDEN.read_text(encoding="utf-8"))
    pairs = g["pairs"]
    # 预计算 difflib 用于分层
    for p in pairs:
        p["_d"] = difflib.SequenceMatcher(None, norm(p["a"]), norm(p["b"])).ratio()
    hard = [p for p in pairs if p["label"] == "same" and p["_d"] < 0.5]
    easy = [p for p in pairs if p["label"] == "same" and p["_d"] >= 0.5]
    sim = [p for p in pairs if p["label"] == "similar"]
    dis = [p for p in pairs if p["label"] == "distinct"]
    print(f"分层：hard_same(difflib<0.5)={len(hard)}  easy_same={len(easy)}  "
          f"similar={len(sim)}  distinct={len(dis)}\n")

    from lib import similarity
    from lib.similarity import OnnxEmbedder

    def embed_fn(model, pooling):
        os.environ["MEMORY_ECOLOGY_EMBED_POOLING"] = pooling
        emb = OnnxEmbedder(model)
        cache = {}

        def fn(a, b):
            for t in (a, b):
                cache.setdefault(t, emb.embed(t))
            return sum(x * y for x, y in zip(cache[a], cache[b]))
        return fn

    fns = {}
    os.environ["MEMORY_ECOLOGY_SIM_BACKEND"] = "difflib"
    fns["difflib"] = lambda a, b: similarity.ratio(a, b)
    os.environ["MEMORY_ECOLOGY_SIM_BACKEND"] = "ngram"
    fns["ngram"] = lambda a, b: similarity.ratio(a, b)
    for m in ("bge-small-zh-v1.5", "bge-base-zh-v1.5", "bge-large-zh-v1.5"):
        if (MODELS_ROOT / m / "model.onnx").is_file():
            for pl in ("mean", "cls"):
                fns[f"{m}[{pl}]"] = embed_fn(m, pl)

    # 对每个后端，在"similar 误合并率 ≤ 5%"约束下最大化 hard_same 召回
    print(f"{'后端':<34}{'hard_same召回':>13}{'easy_same':>10}{'similar误合并':>13}{'distinct误合并':>13}{'阈值':>8}")
    for name, fn in fns.items():
        hs = [fn(p["a"], p["b"]) for p in hard]
        es = [fn(p["a"], p["b"]) for p in easy]
        sm = [fn(p["a"], p["b"]) for p in sim]
        ds = [fn(p["a"], p["b"]) for p in dis]
        # 选阈值：similar 误合并 ≤5%（即最多 1~2 对 similar 被判合并）
        best = None
        for t in sorted(set(hs + es + sm + ds)):
            sim_fp = sum(1 for s in sm if s >= t) / max(len(sm), 1)
            if sim_fp > 0.06:
                continue
            rec_h = sum(1 for s in hs if s >= t) / max(len(hs), 1)
            if best is None or rec_h > best[0]:
                best = (rec_h, t, sum(1 for s in es if s >= t) / max(len(es), 1),
                        sim_fp, sum(1 for s in ds if s >= t) / max(len(ds), 1))
        if best is None:
            print(f"{name:<34}{'无可行阈值':>13}")
            continue
        rec_h, t, rec_e, sim_fp, dis_fp = best
        print(f"{name:<34}{rec_h:>12.1%}{rec_e:>10.1%}{sim_fp:>13.1%}{dis_fp:>13.1%}{t:>8.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
