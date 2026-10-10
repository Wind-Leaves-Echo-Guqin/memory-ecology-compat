#!/usr/bin/env python3
"""S7 OR 召回 + RRF 融合回归（v2.4.0，2026-10-07）

背景（落地形态变更，如实记录）：原计划第二路为 FTS5 porter+trigram，实测证伪——
  · porter 是英文词干器，对中文 0 收益；
  · FTS5 trigram 分词器要求查询 ≥3 字符，而中文常用词恰是 2 字（「备份」「框架」全零）；
  · FTS5 unicode61 路与现有子串路结果高度重合，唯一增量是多词 AND 的召回缺口。
故第二路改为零依赖 OR 召回（补部分命中），与 AND 路做 RRF 融合（context-mode 同款 K=60）。
FTS5 降级登记为 >1000 条的规模化路径（ARCHITECTURE_TODO）。

用例：
1. or_recall：任一 term 命中即召回；覆盖度加成让多词全中排在部分命中之前；零命中返回 None
2. rrf_fuse：两路都靠前的条目排最前（双份票）；单路独有靠后补位；同分按 slug 稳定排序
3. --recall 集成：AND 有命中时 OR 独有条目进榜且排在其后；单 term 不触发（避免噪声）；
   默认关闭时行为与既有完全一致（行为锁定）
4. 输出标注：text 显示召回模式；json mode 字段

隔离：全部写临时目录（模块属性 patch）。运行: python test_recall_rrf.py
"""
from __future__ import annotations

import datetime
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import memory_query as mq

TODAY_ISO = datetime.date.today().isoformat()


def _md(slug: str, body: str, status: str = "active", mtype: str = "semantic") -> str:
    return "\n".join([
        "---", f"name: {slug}", f"type: {mtype}", f"status: {status}",
        "occurrences: 1", "session_count: 1",
        f"first_seen: {TODAY_ISO}", f"last_seen: {TODAY_ISO}",
        f"last_hit: {TODAY_ISO}", f"valid_time: {TODAY_ISO}",
        f"transaction_time: {TODAY_ISO}T00:00:00",
        f"last_verified: {TODAY_ISO}", "origin_session_id: ", "superseded_by: ",
        "---", "", body, ""])


class RrfTestBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.detail = self.root / "detail"
        self.archive = self.root / "archive"
        self.detail.mkdir(parents=True)
        self.archive.mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()

    def _write(self, slug: str, body: str, status: str = "active") -> None:
        (self.detail / f"{slug}.md").write_text(_md(slug, body, status), encoding="utf-8")

    def _scan(self):
        return mq.scan_memory_dirs(self.detail, self.archive)

    def _run(self, *argv):
        buf = io.StringIO()
        with mock.patch.object(mq, "DETAIL_DIR", self.detail), \
             mock.patch.object(mq, "ARCHIVE_DIR", self.archive), \
             mock.patch.object(mq, "HITS_LOG", self.root / "h.jsonl"), \
             mock.patch.object(mq, "GATE_LOG_DIR", self.root / "gate_log"), \
             mock.patch.object(mq, "DB", self.root / "eco.db"), \
             redirect_stdout(buf):
            rc = mq.main(list(argv))
        return rc, buf.getvalue()


class TestOrRecall(RrfTestBase):
    def test_any_term_matches(self):
        self._write("备份判据", "备份的判据已定")
        self._write("另一条", "完全无关内容")
        entries = self._scan()
        hits = {e["slug"] for _, e in mq.or_recall(["备份", "架构"], entries)}
        self.assertIn("备份判据", hits)
        self.assertNotIn("另一条", hits)

    def test_coverage_ranks_full_match_first(self):
        # 甲命中两词，乙只命中一词 → 甲必须排在乙前
        self._write("甲", "备份 与 判据 都有")
        self._write("乙", "只有备份两个字")
        entries = self._scan()
        ordered = [e["slug"] for _, e in mq.or_recall(["备份", "判据"], entries)]
        self.assertEqual(ordered[0], "甲")

    def test_zero_match_returns_none(self):
        self._write("甲", "毫不相关")
        entries = self._scan()
        self.assertEqual(mq.or_recall(["词甲", "词乙"], entries), [])


class TestRrfFuse(unittest.TestCase):
    def _e(self, slug: str) -> dict:
        return {"slug": slug, "path": Path(f"/tmp/{slug}.md"), "status": "active",
                "source": "detail", "type": "semantic", "body": slug}

    def test_both_lists_top_wins(self):
        a, b, c = self._e("甲"), self._e("乙"), self._e("丙")
        # 甲在两路都排第一 → 双份票 → 融合后仍第一
        fused = mq.rrf_fuse([[(900, a), (800, b)], [(700, a), (600, c)]])
        self.assertEqual(fused[0][1]["slug"], "甲")

    def test_single_list_entries_follow(self):
        # 甲两路都在（1/61 + 1/62）> 乙 单路第一（1/61）> 丙 单路第二（1/62）
        a, b, c, d = self._e("甲"), self._e("乙"), self._e("丙"), self._e("丁")
        fused = mq.rrf_fuse([[(900, a), (800, d)], [(700, b), (600, a)]])
        order = [e["slug"] for _, e in fused]
        self.assertEqual(order[0], "甲")      # 双路贡献最大
        self.assertEqual(order[-1], "丁")     # 单路末位垫底

    def test_stable_tiebreak_by_slug(self):
        x, y = self._e("乙"), self._e("甲")
        # 两路各有其一、排名相同 → 融合分相同 → 按 slug 稳定排序（码点序：乙 U+4E59 < 甲 U+7532）
        fused = mq.rrf_fuse([[(900, x)], [(900, y)]])
        self.assertEqual([e["slug"] for _, e in fused], ["乙", "甲"])


class TestRecallIntegration(RrfTestBase):
    def test_and_hits_rank_before_or_only(self):
        self._write("全中条目", "备份 判据 双词都中")
        self._write("部分命中条目", "只提到备份")
        rc, out = self._run("备份 判据", "--recall", "--format", "json",
                            "--no-log", "--no-update")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["mode"], "lexical+recall")
        ids = [h["id"] for h in data["hits"]]
        self.assertEqual(ids[0], "全中条目")
        self.assertIn("部分命中条目", ids)

    def test_default_off_behaves_as_before(self):
        self._write("全中条目", "备份 判据 双词都中")
        self._write("部分命中条目", "只提到备份")
        rc, out = self._run("备份 判据", "--format", "json", "--no-log", "--no-update")
        data = json.loads(out)
        self.assertEqual(data["mode"], "lexical")
        ids = [h["id"] for h in data["hits"]]
        self.assertEqual(ids, ["全中条目"])  # 行为锁定：默认不召回部分命中

    def test_single_term_recall_is_noop(self):
        self._write("甲", "备份内容")
        rc, out = self._run("备份", "--recall", "--format", "json", "--no-log", "--no-update")
        data = json.loads(out)
        self.assertEqual(data["mode"], "lexical")  # 单 term 不触发（OR 与 AND 等价）

    def test_text_output_mentions_mode(self):
        self._write("甲", "备份 判据")
        self._write("乙", "只提到备份")
        rc, out = self._run("备份 判据", "--recall", "--no-log", "--no-update")
        self.assertIn("RRF 融合", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
