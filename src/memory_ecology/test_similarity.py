#!/usr/bin/env python3
"""相似度统一层回归（批 5，2026-10-04）。

1. ngram 后端：相同 → 1.0；无关 → 低分；中文同义改写得分显著高于无关对
2. difflib 后端（默认）：与旧行为等价——门①④阈值语义不漂移
3. 后端切换：MEMORY_ECOLOGY_SIM_BACKEND 环境变量生效；embedding 缺依赖自动回退 ngram
运行: python test_similarity.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from lib import similarity


class TestNgram(unittest.TestCase):
    def test_identical_is_one(self):
        self.assertAlmostEqual(similarity.ratio("用户偏好深色主题的编辑器",
                                                "用户偏好深色主题的编辑器"), 1.0, places=6)

    def test_disjoint_is_low(self):
        s = self._ratio_ngram("用户偏好深色主题的代码编辑器",
                              "缓存策略采用 LRU 并设置三十天过期")
        self.assertLess(s, 0.3)

    def test_rewrite_scores_between(self):
        """同义改写 > 无关（ngram 的核心价值：difflib 对这类改写得分不稳定）。"""
        same = self._ratio_ngram("用户偏好深色主题的代码编辑器",
                                 "用户喜欢用深色配色的编辑器写代码")
        distinct = self._ratio_ngram("用户偏好深色主题的代码编辑器",
                                     "缓存策略采用 LRU 并设置三十天过期")
        self.assertGreater(same, distinct + 0.2)
        # 实测注记：bigram 余弦对同义改写给 ~0.36（排序有效但远低于 0.8 合并阈值）——
        # 这正是「词面层只能排序、语义层（L3/LLM 终审）才能判合并」的定量证据，非 bug
        self.assertLess(same, 0.6)

    def test_empty_safe(self):
        self.assertEqual(similarity.ratio("", "x"), 0.0)
        self.assertEqual(similarity.ratio(None, "x"), 0.0)

    @staticmethod
    def _ratio_ngram(a: str, b: str) -> float:
        with mock.patch.dict("os.environ", {"MEMORY_ECOLOGY_SIM_BACKEND": "ngram"}):
            return similarity.ratio(a, b)


class TestBackendSwitch(unittest.TestCase):
    def test_default_is_difflib_locked(self):
        """默认 difflib：门①④阈值语义不漂移（行为锁定）。"""
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(similarity.backend_name(), "difflib")
            a = "用户偏好深色主题的代码编辑器"
            b = "用户喜欢用深色配色的编辑器写代码"
            expect = __import__("difflib").SequenceMatcher(
                None, similarity._norm(a), similarity._norm(b)).ratio()
            self.assertAlmostEqual(similarity.ratio(a, b), expect, places=9)

    def test_embedding_fallback_to_ngram(self):
        """embedding 缺依赖（本环境未装 onnxruntime）→ 自动回退 ngram，不抛异常。"""
        with mock.patch.dict("os.environ",
                             {"MEMORY_ECOLOGY_SIM_BACKEND": "embedding",
                              "MEMORY_ECOLOGY_MODELS": "Z:/definitely/missing"}), \
             mock.patch.object(similarity, "_get_embedder",
                               side_effect=similarity.BackendUnavailable("缺依赖")):
            s = similarity.ratio("用户偏好深色主题", "用户偏好深色主题")
            self.assertGreater(s, 0.99)  # 回退 ngram：相同文本 → 1.0


class TestGateThreshold(unittest.TestCase):
    """门阈值后端感知（2026-10-04 迁移）：换后端阈值自动跟随，缺模型回退也跟随。"""

    def test_difflib_locked(self):
        """默认 difflib：门阈值 = 旧值（行为锁定，不漂移）。"""
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(similarity.gate_threshold("suspect", 0.5), 0.50)
            self.assertEqual(similarity.gate_threshold("merge", 0.8), 0.80)
            self.assertEqual(similarity.gate_threshold("noop", 0.95), 0.95)
            self.assertEqual(similarity.gate_threshold("candidate", 0.7), 0.70)

    def test_ngram_migrated(self):
        with mock.patch.dict("os.environ", {"MEMORY_ECOLOGY_SIM_BACKEND": "ngram"}):
            self.assertEqual(similarity.gate_threshold("merge", 0.8), 0.58)
            self.assertEqual(similarity.gate_threshold("suspect", 0.5), 0.42)

    def test_embedding_migrated(self):
        with mock.patch.dict("os.environ", {"MEMORY_ECOLOGY_SIM_BACKEND": "embedding"}), \
             mock.patch.object(similarity, "effective_backend", return_value="embedding"):
            self.assertEqual(similarity.gate_threshold("merge", 0.8), 0.84)
            self.assertEqual(similarity.gate_threshold("noop", 0.95), 0.97)

    def test_embedding_unavailable_uses_ngram_threshold(self):
        """配置 embedding 但模型缺失 → 分数走 ngram 回退，阈值也必须取 ngram 值
        （否则"阈值按 embedding 校准、分数来自 ngram"静默错配）。"""
        with mock.patch.dict("os.environ",
                             {"MEMORY_ECOLOGY_SIM_BACKEND": "embedding",
                              "MEMORY_ECOLOGY_MODELS": "Z:/definitely/missing"}), \
             mock.patch.object(similarity, "_EMBED_FAILED", False), \
             mock.patch.object(similarity, "_EMBEDDER", None):
            self.assertEqual(similarity.effective_backend(), "ngram")
            self.assertEqual(similarity.gate_threshold("merge", 0.8), 0.58)  # ngram 值

    def test_env_override(self):
        with mock.patch.dict("os.environ", {"MEMORY_ECOLOGY_SIM_THRESHOLD_MERGE": "0.66"}):
            self.assertEqual(similarity.gate_threshold("merge", 0.8), 0.66)

    def test_unknown_role_falls_back(self):
        with mock.patch.dict("os.environ", {"MEMORY_ECOLOGY_SIM_BACKEND": "ngram"}):
            self.assertEqual(similarity.gate_threshold("nonexistent", 0.33), 0.33)


class TestModelPathResolution(unittest.TestCase):
    def test_env_override_wins(self):
        with mock.patch.dict("os.environ", {"MEMORY_ECOLOGY_MODELS": "X:/custom"}):
            self.assertEqual(similarity.models_root(), Path("X:/custom"))

    def test_default_root_is_hermes_not_cwd(self):
        """默认模型根 = <生态根>/models，而非 CWD/models（回归 2026-10-04 踩坑）。

        旧实现用 Path(".")/models：cron 以任意 CWD 启动时静默找不到模型 → 回退 ngram，
        生产 embedding 从未真正生效。本测试在临时 CWD 下断言默认根不随 CWD 漂移。
        """
        import tempfile
        with mock.patch.dict("os.environ", {}, clear=True):
            with tempfile.TemporaryDirectory() as other:
                with mock.patch("os.getcwd", return_value=other):
                    root = Path(similarity.models_root())
                    self.assertNotEqual(str(root).replace("\\", "/"),
                                        other.replace("\\", "/") + "/models")
                    self.assertTrue(str(root).replace("\\", "/").endswith("/models"))


class TestEmbedderCache(unittest.TestCase):
    def test_embed_cache_hit(self):
        """同一文本重复 embed 命中缓存（门④两两比较会重复 embed 同一正文）。"""
        emb = similarity.OnnxEmbedder.__new__(similarity.OnnxEmbedder)
        emb._cache = {}
        calls = []

        class FakeTok:
            def encode(self, s):
                calls.append(s)
                return type("E", (), {"ids": [1, 2, 3]})()

        class FakeSess:
            def run(self, *a, **k):
                import numpy as np
                return [np.ones((1, 3, 4))]  # (batch, seq=max_len, hidden)

        emb.tokenizer, emb.sess = FakeTok(), FakeSess()
        emb.max_len, emb.pooling = 3, "mean"  # 与 ids 等长 → pad=0，mask 形状对齐
        emb.embed("同一文本")
        emb.embed("同一文本")
        self.assertEqual(len(calls), 1)  # 第二次命中缓存，未再编码


class TestIsSame(unittest.TestCase):
    def test_threshold(self):
        with mock.patch.dict("os.environ", {"MEMORY_ECOLOGY_SIM_BACKEND": "ngram"}):
            self.assertTrue(similarity.is_same("部署脚本需要先运行数据库迁移再启动服务",
                                               "部署脚本需要先运行数据库迁移再启动服务", 0.8))
            self.assertFalse(similarity.is_same("用户偏好深色主题的代码编辑器",
                                                "缓存策略采用 LRU 并设置三十天过期", 0.8))


if __name__ == "__main__":
    unittest.main(verbosity=2)
