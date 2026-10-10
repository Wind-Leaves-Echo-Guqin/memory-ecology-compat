#!/usr/bin/env python3
"""阈值重校准：按门①③④ 的三处语义分别求新后端下的阈值。

difflib 口径（旧）：SIM_SUSPECT=0.50（LLM 复核窗口下界）/ SIM_NEAR=0.80（规则兜底合并）
/ 0.95（NOOP 同文）。换 embedding 后三者的数字含义全变，必须按语义重算：

  SIM_SUSPECT  复核窗口：漏 = 静默重复（坏）；误 = 多一次 LLM 调用（可容忍）。
               → 目标：捕获 ≥95% 的 (same ∪ similar)，即"该被复核的都进窗口"。
  SIM_NEAR     规则兜底合并（LLM 不可用时）：误合并 = 丢事实（不可逆）。
               → 目标：FPR=0（distinct+similar 零误合并）下取最低阈值。
  NOOP         同文钳制：目标 = 捕获全部 near-identical（difflib≥0.95 的对）。

用法：python calibrate_thresholds.py [--backend embedding] [--model bge-small-zh-v1.5]
"""
from __future__ import annotations

import argparse
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bge-small-zh-v1.5")
    ap.add_argument("--pooling", default="cls")
    args = ap.parse_args()

    g = json.loads(GOLDEN.read_text(encoding="utf-8"))
    pairs = g["pairs"]
    for p in pairs:
        p["_d"] = difflib.SequenceMatcher(None, norm(p["a"]), norm(p["b"])).ratio()
    same = [p for p in pairs if p["label"] == "same"]
    sim = [p for p in pairs if p["label"] == "similar"]
    dis = [p for p in pairs if p["label"] == "distinct"]

    os.environ["MEMORY_ECOLOGY_EMBED_POOLING"] = args.pooling
    from lib.similarity import OnnxEmbedder
    emb = OnnxEmbedder(args.model)
    cache = {}

    def sc(p):
        for t in (p["a"], p["b"]):
            cache.setdefault(t, emb.embed(t))
        return sum(x * y for x, y in zip(cache[p["a"]], cache[p["b"]]))

    S = [sc(p) for p in same]
    I = [sc(p) for p in sim]
    D = [sc(p) for p in dis]

    print(f"后端：embedding / {args.model} / pooling={args.pooling}")
    print(f"golden {len(pairs)}：same={len(S)} similar={len(I)} distinct={len(D)}\n")

    # SIM_SUSPECT：捕获 ≥95% 的 same∪similar
    review = S + I
    review.sort()
    t_suspect = review[int(len(review) * 0.05)]  # 5 分位 → 95% 在上方
    caught = sum(1 for s in review if s >= t_suspect)
    d_in = sum(1 for s in D if s >= t_suspect)
    print(f"SIM_SUSPECT（复核窗口下界，旧 0.50）")
    print(f"  新值 = {t_suspect:.2f}  → 捕获 {caught}/{len(review)} same∪similar"
          f"（{caught/len(review):.1%}），distinct 误入 {d_in}/{len(D)}"
          f"（{d_in/len(D):.1%}，仅多花 LLM 调用）")

    # SIM_NEAR：FPR=0（distinct+similar 零误合并）下最低阈值
    dmax = max(D + I)
    above = [s for s in S if s > dmax]
    t_near = min(above) if above else dmax + 0.01
    print(f"\nSIM_NEAR（规则兜底合并，旧 0.80）")
    print(f"  新值 = {t_near:.2f}  → FPR=0，回收 {len(above)}/{len(S)} same"
          f"（{len(above)/len(S):.1%}）")
    print(f"  （对照：difflib 口径同法 = "
          f"{min([p['_d'] for p in same if p['_d'] > max(p2['_d'] for p2 in sim+dis)] or [1.0]):.2f}）")

    # NOOP：difflib≥0.95 的对在 embedding 下的最低分
    near = [sc(p) for p in pairs if p["_d"] >= 0.95]
    if near:
        t_noop = min(near)
        print(f"\nNOOP（同文钳制，旧 0.95）")
        print(f"  新值 = {t_noop:.2f}  → 覆盖全部 {len(near)} 对 difflib≥0.95 的对")

    print(f"\n建议写入生产配置：")
    print(f"  MEMORY_ECOLOGY_SIM_BACKEND=embedding")
    print(f"  MEMORY_ECOLOGY_EMBED_MODEL={args.model}")
    print(f"  MEMORY_ECOLOGY_EMBED_POOLING={args.pooling}")
    print(f"  SIM_SUSPECT={t_suspect:.2f}  SIM_NEAR={t_near:.2f}"
          + (f"  NOOP={t_noop:.2f}" if near else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
