#!/usr/bin/env python3
"""下载候选 embedding 模型（ONNX）到 models/<name>/（三件套：model.onnx + tokenizer.json + config.json）。

走 HF 镜像（HF_ENDPOINT=https://hf-mirror.com）。用法：
  python fetch_embed_models.py bge-small-zh-v1.5 bge-base-zh-v1.5 ...
  python fetch_embed_models.py --list
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# 名称 -> (HF repo, onnx 文件在 repo 内的路径)
CATALOG = {
    "bge-small-zh-v1.5": ("Xenova/bge-small-zh-v1.5", "onnx/model.onnx"),
    "bge-base-zh-v1.5": ("Xenova/bge-base-zh-v1.5", "onnx/model.onnx"),
    "bge-large-zh-v1.5": ("Xenova/bge-large-zh-v1.5", "onnx/model.onnx"),
    "text2vec-base-chinese-paraphrase": ("Xenova/text2vec-base-chinese-paraphrase", "onnx/model.onnx"),
    "text2vec-base-chinese": ("shibing624/text2vec-base-chinese", "onnx/model.onnx"),
    "paraphrase-multilingual-MiniLM-L12-v2": ("Xenova/paraphrase-multilingual-MiniLM-L12-v2", "onnx/model.onnx"),
}

def _hermes_models() -> Path:
    """模型根 = <生态根>/models（经 lib.config.hermes_root() 单源派生，跨树一致）。"""
    try:
        from lib.config import hermes_root
        return hermes_root() / "models"
    except Exception:
        return HERE.parent.parent / "models"


MODELS_ROOT = Path(os.environ.get("MEMORY_ECOLOGY_MODELS", _hermes_models()))


def fetch(name: str) -> bool:
    from huggingface_hub import hf_hub_download
    repo, onnx_path = CATALOG[name]
    dest = MODELS_ROOT / name
    dest.mkdir(parents=True, exist_ok=True)
    print(f"[{name}] {repo}")
    for local, remote in [("model.onnx", onnx_path),
                          ("tokenizer.json", "tokenizer.json"),
                          ("config.json", "config.json")]:
        try:
            p = hf_hub_download(repo_id=repo, filename=remote)
            shutil.copyfile(p, dest / local)
            print(f"  ✓ {local} ({(dest / local).stat().st_size / 1e6:.1f} MB)")
        except Exception as e:
            print(f"  ✗ {remote}: {type(e).__name__}: {str(e)[:100]}")
            if local == "model.onnx":
                return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list or not args.names:
        for k, (r, o) in CATALOG.items():
            print(f"  {k:<40} {r}  ({o})")
        return 0
    ok = 0
    for n in args.names:
        if n not in CATALOG:
            print(f"未知模型 {n}（--list 查看）")
            continue
        ok += bool(fetch(n))
    print(f"\n完成 {ok}/{len(args.names)}，目录：{MODELS_ROOT}")
    return 0 if ok == len(args.names) else 1


if __name__ == "__main__":
    sys.exit(main())
