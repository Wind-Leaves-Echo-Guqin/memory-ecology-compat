"""similarity — 相似度统一层（批 5，2026-10-04）。

项目此前 4 处 difflib + 前缀包含各自为政（类型 C 单源裂缝）。本层统一入口：

    from lib.similarity import ratio, is_same
    score = ratio(text_a, text_b)          # 0..1，越高越相似
    is_same(a, b, threshold=0.8)           # 阈值判定

后端分层（MEMORY_ECOLOGY_SIM_BACKEND 环境变量选择，默认 difflib 行为锁定）：
  L0 "difflib"    — SequenceMatcher 字符级 ratio（旧行为，默认：阈值语义不漂移）
  L1 "ngram"      — 字符 2/3-gram 余弦（纯 stdlib，对中文同义改写/语序鲁棒，快一个量级）
  L3 "embedding"  — 本地 ONNX embedding（可选依赖 onnxruntime+tokenizers+模型文件；
                    缺依赖自动回退 L1 并打印一次提示——保证"必须离线可用"由 L1 兜底）

换内核前先跑 eco_sim_eval.py（golden set 评测）——没有标尺的阈值迁移是盲调。
零强制依赖：embedding 相关 import 全部惰性，缺失不影响核心管道。
"""
from __future__ import annotations

import difflib
import math
import os
from collections import Counter

_BACKEND_UNAVAILABLE = {}  # backend name -> 提示信息（只打印一次）


def _norm(s: str) -> str:
    if not s:
        return ""
    return "".join(ch.lower() for ch in s if ch.isalnum())


def ngram_counter(s: str, n: int = 2) -> Counter:
    """字符 n-gram 计数。超短串（len < n）退化为单字符 gram。"""
    s = _norm(s)
    if not s:
        return Counter()
    if len(s) < n:
        return Counter(s)
    return Counter(s[i:i + n] for i in range(len(s) - n + 1))


def cosine(c1: Counter, c2: Counter) -> float:
    if not c1 or not c2:
        return 0.0
    common = set(c1) & set(c2)
    dot = sum(c1[k] * c2[k] for k in common)
    n1 = math.sqrt(sum(v * v for v in c1.values()))
    n2 = math.sqrt(sum(v * v for v in c2.values()))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


# ── L3：本地 ONNX embedding（可选依赖，惰性加载）─────────────────────

class OnnxEmbedder:
    """本地 embedding 后端（必须离线可用：模型文件本地加载，无网络调用）。

    依赖：onnxruntime + tokenizers（pip 可选安装）+ 模型目录
    （MEMORY_ECOLOGY_MODELS 环境变量，默认 <生态根>/models/<model_name>/，
    含 model.onnx + tokenizer.json + config.json）。缺失抛 BackendUnavailable。
    """

    def __init__(self, model_name: str = "bge-small-zh-v1.5"):
        self.model_name = model_name
        try:
            import onnxruntime as ort  # noqa: F401
            from tokenizers import Tokenizer  # noqa: F401
        except ImportError as e:
            raise BackendUnavailable(f"缺依赖（{e}）——pip install onnxruntime tokenizers") from e
        from pathlib import Path
        root = os.environ.get("MEMORY_ECOLOGY_MODELS")
        base = Path(root) if root else Path(".").resolve() / "models"
        self.model_dir = base / model_name
        if not (self.model_dir / "model.onnx").is_file():
            raise BackendUnavailable(f"模型文件缺失：{self.model_dir}（见 models/README.md）")
        self.sess = ort.InferenceSession(str(self.model_dir / "model.onnx"),
                                         providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        self.max_len = 512

    def embed(self, text: str) -> list[float]:
        enc = self.tokenizer.encode((text or "")[:2000])
        ids = enc.ids[:self.max_len]
        mask = [1] * len(ids)
        pad = self.max_len - len(ids)
        ids += [0] * pad
        mask += [0] * pad
        feeds = {
            "input_ids": [ids],
            "attention_mask": [mask],
            "token_type_ids": [[0] * self.max_len],
        }
        # 部分模型不含 token_type_ids 输入——逐个裁剪重试
        try:
            out = self.sess.run(None, feeds)
        except Exception:
            feeds.pop("token_type_ids", None)
            out = self.sess.run(None, feeds)
        vec = out[0][0]
        # mean pooling（简化：取 [CLS] 或均值中较稳的均值；BGE 系列推荐 CLS，均值对短句近似）
        import numpy as _np  # onnxruntime 的输出是 numpy 数组——此处依赖必然已装
        v = _np.asarray(vec)
        if v.ndim == 2:
            m = _np.asarray(mask, dtype=float)[:, None]
            v = (v * m).sum(0) / max(m.sum(), 1.0)
        v = v.tolist()
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


class BackendUnavailable(RuntimeError):
    pass


_EMBEDDER = None  # 惰性单例


def _get_embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = OnnxEmbedder(os.environ.get("MEMORY_ECOLOGY_EMBED_MODEL",
                                                "bge-small-zh-v1.5"))
    return _EMBEDDER


# ── 统一入口 ──────────────────────────────────────────────────────────

def backend_name() -> str:
    return os.environ.get("MEMORY_ECOLOGY_SIM_BACKEND", "difflib").strip().lower()


def ratio(a: str, b: str) -> float:
    """相似度 0..1（越高越相似）。按 backend 分派；embedding 缺依赖自动回退 ngram。"""
    if a is None or b is None:
        return 0.0
    a, b = str(a), str(b)
    if not a.strip() or not b.strip():
        return 0.0
    be = backend_name()
    if be == "difflib":
        return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()
    if be == "ngram":
        return cosine(ngram_counter(a), ngram_counter(b))
    if be == "embedding":
        try:
            emb = _get_embedder()
            va, vb = emb.embed(a), emb.embed(b)
            return sum(x * y for x, y in zip(va, vb))
        except BackendUnavailable as e:
            if be not in _BACKEND_UNAVAILABLE:
                _BACKEND_UNAVAILABLE[be] = True
                print(f"⚠️ similarity: embedding 后端不可用（{e}），回退 ngram")
            return cosine(ngram_counter(a), ngram_counter(b))
    # 未知后端 → 旧行为兜底
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def is_same(a: str, b: str, threshold: float = 0.8) -> bool:
    return ratio(a, b) >= threshold
