#!/usr/bin/env python3
"""S4 检索质量报告回归（v2.3.0，2026-10-07）

1. load_records：坏 JSON 行跳过、非 dict 跳过、文件缺失返回空
2. aggregate：按日计数、零结果率、平均命中、窗口过滤（--days）
3. 零结果查询 Top：首尾空格归并分组、按次数排序
隔离：全部内存/临时文件。运行: python test_retrieval_report.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import eco_retrieval_report as rr


class TestLoadRecords(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(rr.load_records(Path("Z:/definitely/not/here.jsonl")), [])

    def test_bad_lines_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "h.jsonl"
            p.write_text("\n".join([
                json.dumps({"ts": "2026-10-07T10:00:00", "query": "a", "hits": ["x"]}),
                "{broken json",
                json.dumps(["not", "a", "dict"]),
                "",
            ]), encoding="utf-8")
            recs = rr.load_records(p)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["query"], "a")


class TestAggregate(unittest.TestCase):
    def _rec(self, ts, query, hits):
        return {"ts": ts, "query": query, "hits": hits}

    def test_daily_counts_and_rates(self):
        recs = [
            self._rec("2026-10-07T10:00:00", "备份", ["a", "b"]),
            self._rec("2026-10-07T11:00:00", "不存在", []),
            self._rec("2026-10-06T09:00:00", "Vue 迁移", []),
            self._rec("2026-10-06T10:00:00", "缓存", ["c"]),
        ]
        agg = rr.aggregate(recs, days=30, top=5)
        self.assertEqual(agg["total_queries"], 4)
        self.assertEqual(agg["zero_queries"], 2)
        self.assertEqual(agg["zero_rate"], 0.5)
        days = {d["date"]: d for d in agg["by_day"]}
        self.assertEqual(days["2026-10-07"]["total"], 2)
        self.assertEqual(days["2026-10-07"]["avg_hits"], 1.0)
        self.assertEqual(days["2026-10-06"]["zero_rate"], 0.5)

    def test_window_filters_old(self):
        recs = [
            self._rec("2026-10-07T10:00:00", "新查询", []),
            self._rec("2026-01-01T10:00:00", "老查询", []),
        ]
        agg = rr.aggregate(recs, days=30, top=5)
        self.assertEqual(agg["total_queries"], 1)
        self.assertEqual(agg["by_day"][0]["date"], "2026-10-07")

    def test_top_zero_queries_grouped_by_stripped_query(self):
        recs = [
            self._rec("2026-10-07T10:00:00", " Vue 迁移 ", []),
            self._rec("2026-10-07T11:00:00", "Vue 迁移", []),
            self._rec("2026-10-07T12:00:00", "别的词", []),
            self._rec("2026-10-07T13:00:00", "有命中的", ["x"]),
        ]
        agg = rr.aggregate(recs, days=30, top=5)
        top = agg["top_zero_queries"]
        self.assertEqual(top[0], {"query": "Vue 迁移", "count": 2})
        self.assertEqual(len(top), 2)

    def test_empty_records(self):
        agg = rr.aggregate([], days=30, top=5)
        self.assertEqual(agg["total_queries"], 0)
        self.assertEqual(agg["zero_rate"], 0.0)
        self.assertEqual(agg["by_day"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
