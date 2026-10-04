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


class TestIsSame(unittest.TestCase):
    def test_threshold(self):
        with mock.patch.dict("os.environ", {"MEMORY_ECOLOGY_SIM_BACKEND": "ngram"}):
            self.assertTrue(similarity.is_same("部署脚本需要先运行数据库迁移再启动服务",
                                               "部署脚本需要先运行数据库迁移再启动服务", 0.8))
            self.assertFalse(similarity.is_same("用户偏好深色主题的代码编辑器",
                                                "缓存策略采用 LRU 并设置三十天过期", 0.8))


if __name__ == "__main__":
    unittest.main(verbosity=2)
