#!/usr/bin/env python3
"""S6 向量缓存 shadow index 回归（v2.4.0，2026-10-07）

1. VectorCache.sync：首建全量嵌入；content_hash 未变 → 读缓存（embed 调用数不增）；
   内容变 → 只重嵌该条；模型名变 → 整库失效重建
2. prune：源消失的行被删除；stats 口径
3. cosine：归一化点积；长度不齐/零向量返回 0
4. memory_query --semantic：mock embedder 后按余弦排序（词面不命中也能召回——同义改写）；
   后端不可用回退词面打分并提示；json 带 mode 字段
5. BLOB 往返（array('f')，零 numpy）

隔离：全部写临时目录/mock。运行: python test_vector_cache.py
"""
from __future__ import annotations

import datetime
import io
import json
import math
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from lib import vector_cache as vc_mod
from lib.vector_cache import VectorCache, cosine, content_hash, entry_text

import memory_query as mq

TODAY_ISO = datetime.date.today().isoformat()


_FAKE_DIMS = {"前端": 0, "框架": 1, "vue": 2, "单页": 3, "红烧": 4, "焯水": 5}


def fake_embed(text: str) -> list[float]:
    """受控假向量：文本命中关键词表则对应维度置 1，L2 归一化——
    确定性且语义可预测（共享关键词多的文本对余弦更高），足够支撑断言。"""
    v = [0.0] * 8
    t = (text or "").lower()
    for k, i in _FAKE_DIMS.items():
        if k in t:
            v[i] = 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _entry(root: Path, slug: str, body: str, source: str = "detail") -> dict:
    p = root / f"{slug}.md"
    if not p.exists():
        p.write_text(f"---\nname: {slug}\nstatus: active\n---\n{body}\n", encoding="utf-8")
    return {"path": p, "slug": slug, "body": body, "source": source,
            "status": "active", "raw": p.read_text(encoding="utf-8")}


class TestVectorCache(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.db = self.root / ".vector_cache.db"

    def tearDown(self):
        self._td.cleanup()

    def test_sync_build_then_cache_hit(self):
        e = _entry(self.root, "条目甲", "正文内容一")
        vc = VectorCache(self.db, "fake-model")
        calls = {"n": 0}

        def embed(t):
            calls["n"] += 1
            return fake_embed(t)

        vecs = vc.sync([e], embed)
        self.assertEqual(calls["n"], 1)
        self.assertIn(str(e["path"]), vecs)
        # 第二次：内容未变 → 读缓存，不再调用 embed（float32 BLOB 往返有 1e-7 级精度差）
        vecs2 = vc.sync([e], embed)
        self.assertEqual(calls["n"], 1)
        self.assertTrue(all(abs(a - b) < 1e-5 for a, b in
                            zip(vecs[str(e["path"])], vecs2[str(e["path"])])))
        # 内容变 → 只重嵌该条
        e2 = _entry(self.root, "条目甲", "正文内容一改")
        vc.sync([e2], embed)
        self.assertEqual(calls["n"], 2)

    def test_model_change_invalidates_all(self):
        e = _entry(self.root, "条目乙", "正文内容二")
        calls = {"n": 0}

        def embed(t):
            calls["n"] += 1
            return fake_embed(t)

        VectorCache(self.db, "model-a").sync([e], embed)
        VectorCache(self.db, "model-b").sync([e], embed)
        self.assertEqual(calls["n"], 2)  # 换模型 → 整库失效重嵌
        self.assertEqual(VectorCache(self.db, "model-b").stats()["rows"], 1)

    def test_prune_removes_stale(self):
        e1 = _entry(self.root, "条目丙", "正文三")
        e2 = _entry(self.root, "条目丁", "正文四")
        vc = VectorCache(self.db, "fake-model")
        vc.sync([e1, e2], fake_embed)
        self.assertEqual(vc.stats()["rows"], 2)
        removed = vc.prune([e1["path"]])
        self.assertEqual(removed, 1)
        self.assertEqual(vc.stats()["rows"], 1)

    def test_embed_failure_skips_entry(self):
        e = _entry(self.root, "条目戊", "正文五")

        def bad_embed(t):
            raise RuntimeError("模型炸了")

        vc = VectorCache(self.db, "fake-model")
        vecs = vc.sync([e], bad_embed)  # 不抛异常；该条缺席
        self.assertEqual(vecs, {})

    def test_cosine_and_blob_roundtrip(self):
        v = fake_embed("前端框架")  # 命中 前端/框架 两维（零向量余弦恒 0，不能用作样本）
        self.assertAlmostEqual(cosine(v, v), 1.0, places=6)
        self.assertEqual(cosine(v, []), 0.0)
        self.assertEqual(cosine(v, v[:4]), 0.0)  # 维度不齐
        from array import array
        blob = array("f", v).tobytes()
        arr = array("f")
        arr.frombytes(blob)
        self.assertTrue(all(abs(a - b) < 1e-5 for a, b in zip(list(arr), v)))  # float32 精度

    def test_entry_text_hash_stable(self):
        e = {"slug": "名", "body": "体"}
        self.assertEqual(entry_text(e), "名\n体")
        self.assertEqual(content_hash(e), content_hash({"slug": "名", "body": "体"}))
        self.assertNotEqual(content_hash(e), content_hash({"slug": "名", "body": "体2"}))


class FakeEmbedder:
    model_name = "fake-model"

    def embed(self, text):
        return fake_embed(text)


class TestSemanticMode(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.detail = self.root / "detail"
        self.detail.mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()

    def _write(self, slug, body):
        (self.detail / f"{slug}.md").write_text(
            f"---\nname: {slug}\ntype: semantic\nstatus: active\noccurrences: 1\n"
            f"session_count: 1\nfirst_seen: {TODAY_ISO}\nlast_seen: {TODAY_ISO}\n"
            f"last_hit: {TODAY_ISO}\nvalid_time: {TODAY_ISO}\ntransaction_time: {TODAY_ISO}T00:00:00\n"
            f"last_verified: {TODAY_ISO}\norigin_session_id: \nsuperseded_by: \n---\n{body}\n",
            encoding="utf-8")

    def _run_main(self, *argv):
        buf = io.StringIO()
        with mock.patch.object(mq, "DETAIL_DIR", self.detail), \
             mock.patch.object(mq, "ARCHIVE_DIR", self.root / "archive"), \
             mock.patch.object(mq, "HITS_LOG", self.root / "h.jsonl"), \
             mock.patch.object(mq, "GATE_LOG_DIR", self.root / "gate_log"), \
             mock.patch.object(mq, "VECTOR_CACHE_DB", self.root / ".vector_cache.db"), \
             redirect_stdout(buf):
            rc = mq.main(list(argv))
        return rc, buf.getvalue()

    def test_semantic_recalls_paraphrase(self):
        # 词面零重叠：查询"前端框架"，条目正文"项目用 Vue 构建"——词面 AND 不命中，向量召回
        self._write("技术选型", "项目前端用 Vue 构建单页应用")
        self._write("做饭心得", "红烧肉要先焯水再炖四十分钟")
        with mock.patch("lib.similarity.effective_backend", return_value="embedding"), \
             mock.patch("lib.similarity._get_embedder", return_value=FakeEmbedder()):
            rc, out = self._run_main("前端框架", "--semantic", "--format", "json",
                                     "--no-log", "--no-update")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["mode"], "semantic")
        self.assertEqual(data["hits"][0]["id"], "技术选型")

    def test_semantic_fallback_when_backend_unavailable(self):
        self._write("词面条目", "备份策略关键词正文")
        with mock.patch("lib.similarity.effective_backend", return_value="ngram"):
            rc, out = self._run_main("备份", "--semantic", "--no-log", "--no-update")
        self.assertIn("回退词面", out)
        self.assertIn("备份策略关键词正文", out)  # 词面路径照常命中

    def test_lexical_json_has_mode(self):
        self._write("普通条目", "独特词甲乙丙丁")
        rc, out = self._run_main("独特词", "--format", "json", "--no-log", "--no-update")
        self.assertEqual(json.loads(out)["mode"], "lexical")

    def test_window_filter_does_not_purge_cache(self):
        """回归（2026-10-07 OCR）：--semantic + 时间窗过滤时，prune 的保留集必须是
        全量条目而非过滤后的条目——否则窗口外条目的向量被误删，下次无窗查询被迫全量重嵌。"""
        old_day = (datetime.date.today() - datetime.timedelta(days=200)).isoformat()
        self._write("新条目", "新近内容")
        (self.detail / "旧条目.md").write_text(
            f"---\nname: 旧条目\ntype: semantic\nstatus: active\noccurrences: 1\n"
            f"session_count: 1\nfirst_seen: {old_day}\nlast_seen: {old_day}\n"
            f"last_hit: {old_day}\nvalid_time: {old_day}\ntransaction_time: {old_day}T00:00:00\n"
            f"last_verified: {old_day}\norigin_session_id: \nsuperseded_by: \n---\n陈旧内容\n",
            encoding="utf-8")
        with mock.patch("lib.similarity.effective_backend", return_value="embedding"), \
             mock.patch("lib.similarity._get_embedder", return_value=FakeEmbedder()):
            # ① 先全量查询一次：两条都进缓存
            rc, _ = self._run_main("新近内容", "--semantic",
                                   "--format", "json", "--no-log", "--no-update")
            self.assertEqual(rc, 0)
            vc = VectorCache(self.root / ".vector_cache.db", "fake-model")
            self.assertEqual(vc.stats()["rows"], 2)
            # ② 再带时间窗查询：窗口只含新条目，但 prune 保留集是全量 → 旧条目缓存不得被删
            rc, _ = self._run_main("新近内容", "--semantic", "--window", "30",
                                   "--format", "json", "--no-log", "--no-update")
            self.assertEqual(rc, 0)
            self.assertEqual(vc.stats()["rows"], 2,
                             "时间窗过滤不得删除窗口外条目的向量缓存")


if __name__ == "__main__":
    unittest.main(verbosity=2)
