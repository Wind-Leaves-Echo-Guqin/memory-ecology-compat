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

def models_root() -> "os.PathLike | object":
    """模型根目录：MEMORY_ECOLOGY_MODELS 优先，否则 <生态根>/models。

    旧实现用 Path(".")/models（CWD 相对）——cron 以任意 CWD 启动时静默找不到模型
    而回退 ngram（2026-10-04 实测踩坑：生产 embedding 从未真正生效）。改经
    lib.config.hermes_root() 单源派生，跨树/跨 CWD 一致。
    """
    from pathlib import Path
    root = os.environ.get("MEMORY_ECOLOGY_MODELS")
    if root:
        return Path(root)
    try:
        from lib.config import hermes_root
        return hermes_root() / "models"
    except Exception:
        return Path(__file__).resolve().parent.parent.parent / "models"


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
        self.model_dir = models_root() / model_name
        if not (self.model_dir / "model.onnx").is_file():
            raise BackendUnavailable(f"模型文件缺失：{self.model_dir}（见 models/README.md）")
        self.sess = ort.InferenceSession(str(self.model_dir / "model.onnx"),
                                         providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        self.max_len = 512
        # pooling：mean（默认，向后兼容）/ cls（BGE 系列官方推荐，经 golden 实测校准后选定）
        self.pooling = os.environ.get("MEMORY_ECOLOGY_EMBED_POOLING", "mean").strip().lower()
        self._cache: dict[str, list[float]] = {}  # 文本→向量（门④两两比较同一正文会重复 embed）

    def embed(self, text: str) -> list[float]:
        key = text or ""
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        enc = self.tokenizer.encode(key[:2000])
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
        # pooling：mean（默认）/ cls（BGE 官方推荐）。经 golden 实测校准后由
        # MEMORY_ECOLOGY_EMBED_POOLING 选择；两者对短句差异小，长句 CLS 更稳。
        import numpy as _np  # onnxruntime 的输出是 numpy 数组——此处依赖必然已装
        v = _np.asarray(vec)
        if self.pooling == "cls" and v.ndim == 2:
            v = v[0]
        elif v.ndim == 2:
            m = _np.asarray(mask, dtype=float)[:, None]
            v = (v * m).sum(0) / max(m.sum(), 1.0)
        v = v.tolist()
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out_vec = [x / n for x in v]
        self._cache[key] = out_vec
        return out_vec


class BackendUnavailable(RuntimeError):
    pass


_EMBEDDER = None  # 惰性单例
_EMBED_FAILED = False  # embedding 加载失败（缺依赖/缺模型）——缓存，避免每对重试


def _get_embedder():
    global _EMBEDDER, _EMBED_FAILED
    if _EMBEDDER is None:
        if _EMBED_FAILED:
            raise BackendUnavailable("embedding 后端此前加载失败（见首次告警）")
        try:
            _EMBEDDER = OnnxEmbedder(os.environ.get("MEMORY_ECOLOGY_EMBED_MODEL",
                                                    "bge-small-zh-v1.5"))
        except BackendUnavailable:
            _EMBED_FAILED = True
            raise
    return _EMBEDDER


# ── 统一入口 ──────────────────────────────────────────────────────────

def backend_name() -> str:
    return os.environ.get("MEMORY_ECOLOGY_SIM_BACKEND", "difflib").strip().lower()


# 门阈值后端映射（2026-10-04 用 golden 182 对实测迁移，见 scripts/migrate_thresholds.py）。
# 迁移原则：保持旧 difflib 阈值的"操作点"（捕获率/误合并率）不变，只换刻度。
# 键 = 门的语义角色；值 = {后端: 阈值}。difflib 列 = 旧值（行为锁定，校验用）。
GATE_THRESHOLDS = {
    # 门① 疑似窗口下界（漏=静默重复；误=多一次 LLM 调用，可容忍）→ 捕获 60% same∪similar
    "suspect": {"difflib": 0.50, "ngram": 0.42, "embedding": 0.76},
    # 门①③ 规则兜底合并（误合并=丢事实，不可逆）→ FPR=0 下最低阈值
    "merge": {"difflib": 0.80, "ngram": 0.58, "embedding": 0.84},
    # 门① 同文钳制 NOOP → 覆盖全部 difflib≥0.95 的对
    "noop": {"difflib": 0.95, "ngram": 0.90, "embedding": 0.97},
    # 门④ 合并候选清单（只出清单不执行，人工兜底）→ 召回 32% same
    "candidate": {"difflib": 0.70, "ngram": 0.70, "embedding": 0.88},
}


def effective_backend() -> str:
    """实际生效的后端：配置 embedding 但不可用时返回回退后的 "ngram"。

    门阈值必须按"实际打分者"取——否则会出现"阈值按 embedding 校准、分数却来自
    ngram 回退"的静默错配（比不换后端更危险）。故 gate_threshold 用本函数。
    """
    be = backend_name()
    if be == "embedding":
        try:
            _get_embedder()
        except BackendUnavailable:
            return "ngram"
    return be


def gate_threshold(role: str, default: float) -> float:
    """按当前生效后端取门阈值（role 见 GATE_THRESHOLDS）。未知 role/后端回 default。

    门脚本一律经此取阈值——换后端时阈值自动跟随，杜绝"换内核忘改阈值"的静默漂移；
    配置 embedding 但模型/依赖缺失时按回退后的 ngram 取值（与实际打分者一致）。
    也可用环境变量 MEMORY_ECOLOGY_SIM_THRESHOLD_<ROLE> 显式覆盖（调试/微调用）。
    """
    ov = os.environ.get(f"MEMORY_ECOLOGY_SIM_THRESHOLD_{role.upper()}")
    if ov:
        try:
            return float(ov)
        except ValueError:
            pass
    return GATE_THRESHOLDS.get(role, {}).get(effective_backend(), default)


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
