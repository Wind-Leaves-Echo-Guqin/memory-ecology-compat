#!/usr/bin/env python3
"""S1 时间维检索回归（v2.3.0，2026-10-07）

1. 时间衰减三档半连续：≤7d +10 / ≤30d +5 / 31–120d 线性 5→0 / >120d 0
2. 回退链：无 last_hit 的旧格式条目走 last_seen→valid_time→first_seen
3. event_date 发生时间链：valid_time → transaction_time → first_seen → None
4. --since/--until/--window 过滤：边界含/不含、无时态条目跳过计数、非法日期报错
5. json 输出带 valid_time/transaction_time 与 time_filter 元数据

隔离：全部写临时目录（模块级路径属性 patch）。运行: python test_memory_query_time.py
"""
from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import memory_query as mq

TODAY = datetime.date.today()


def _md(slug: str, body: str, status: str = "active", mtype: str = "semantic",
        last_hit: str | None = None, last_seen: str | None = None,
        valid_time: str | None = None, transaction_time: str | None = None,
        first_seen: str | None = None) -> str:
    lines = ["---", f"name: {slug}", f"type: {mtype}", f"status: {status}",
             "occurrences: 3", "session_count: 2"]
    if last_seen:
        lines.append(f"last_seen: {last_seen}")
    if last_hit:
        lines.append(f"last_hit: {last_hit}")
    if valid_time:
        lines.append(f"valid_time: {valid_time}")
    if transaction_time:
        lines.append(f"transaction_time: {transaction_time}")
    if first_seen:
        lines.append(f"first_seen: {first_seen}")
    lines.append("last_verified: 2026-09-01")
    lines += ["---", "", body, ""]
    return "\n".join(lines)


class TestTimeBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.detail = Path(self._td.name) / "detail"
        self.archive = Path(self._td.name) / "archive"
        self.detail.mkdir(parents=True)
        self.archive.mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()

    def _write(self, slug: str, text: str) -> None:
        (self.detail / f"{slug}.md").write_text(text, encoding="utf-8")

    def _scan(self):
        return mq.scan_memory_dirs(self.detail, self.archive)


class TestTimeDecay(TestTimeBase):
    def test_decay_tiers(self):
        """≤7d +10、≤30d +5、31–120d 线性带、>120d 0（同名同正文只差时间）。"""
        cases = {
            "条目近": (TODAY - datetime.timedelta(days=3)).isoformat(),
            "条目月": (TODAY - datetime.timedelta(days=20)).isoformat(),
            "条目季": (TODAY - datetime.timedelta(days=60)).isoformat(),
            "条目陈": (TODAY - datetime.timedelta(days=200)).isoformat(),
        }
        for slug, last_seen in cases.items():
            self._write(slug, _md(slug, "共同关键词正文", last_seen=last_seen))
        scored = {e["slug"]: mq.score_entry(["关键词"], e) for e in self._scan()}
        self.assertGreater(scored["条目近"], scored["条目月"])
        self.assertGreater(scored["条目月"], scored["条目季"])
        self.assertGreater(scored["条目季"], scored["条目陈"])
        # 线性带数值抽查：60 天 → 5*(120-60)/90 ≈ 3
        self.assertEqual(scored["条目季"] - scored["条目陈"], 3)

    def test_fallback_chain_no_last_hit(self):
        """旧格式无 last_hit：last_seen 缺失时回退 valid_time，再回退 first_seen。"""
        self._write("条目甲", _md("条目甲", "关键词正文",
                                  valid_time=(TODAY - datetime.timedelta(days=2)).isoformat()))
        self._write("条目乙", _md("条目乙", "关键词正文",
                                  first_seen=(TODAY - datetime.timedelta(days=2)).isoformat()))
        scored = {e["slug"]: mq.score_entry(["关键词"], e) for e in self._scan()}
        # 两者都经回退链拿到 2 天前 → 同获 +10 档（≥ 与"昨天"条目同级）
        self.assertEqual(scored["条目甲"], scored["条目乙"])
        fresh = _md("条目丙", "关键词正文", last_seen=TODAY.isoformat())
        (self.detail / "条目丙.md").write_text(fresh, encoding="utf-8")
        scored = {e["slug"]: mq.score_entry(["关键词"], e) for e in self._scan()}
        self.assertGreaterEqual(scored["条目甲"], scored["条目丙"])  # 2d(+10) vs 0d(+10)

    def test_event_date_chain(self):
        e = {"valid_time": "2026-09-01", "transaction_time": "2026-09-02",
             "first_seen": "2026-09-03"}
        self.assertEqual(mq.event_date(e), datetime.date(2026, 9, 1))
        self.assertEqual(mq.event_date({"transaction_time": "2026-09-02T10:00:00"}),
                         datetime.date(2026, 9, 2))  # 截取日期部分
        self.assertEqual(mq.event_date({"first_seen": "2026-09-03"}),
                         datetime.date(2026, 9, 3))
        self.assertIsNone(mq.event_date({}))
        self.assertIsNone(mq.event_date({"valid_time": "garbage"}))


class TestTimeFilter(TestTimeBase):
    def setUp(self):
        super().setUp()
        self._write("条目新", _md("条目新", "关键词正文",
                                  valid_time=(TODAY - datetime.timedelta(days=5)).isoformat()))
        self._write("条目旧", _md("条目旧", "关键词正文",
                                  valid_time=(TODAY - datetime.timedelta(days=200)).isoformat()))
        self._write("条目无时", _md("条目无时", "关键词正文"))

    def _main(self, *extra):
        log = Path(self._td.name) / "hits.jsonl"
        buf = __import__("io").StringIO()
        with mock.patch.object(mq, "DETAIL_DIR", self.detail), \
             mock.patch.object(mq, "ARCHIVE_DIR", self.archive), \
             mock.patch.object(mq, "HITS_LOG", log), \
             mock.patch.object(mq, "GATE_LOG_DIR", Path(self._td.name) / "gate_log"), \
             mock.patch.object(mq, "DB", Path(self._td.name) / "eco.db"), \
             mock.patch("sys.stdout", buf):
            rc = mq.main(list(extra))
        return rc, buf.getvalue()

    def test_window_filters_old_and_undated(self):
        rc, out = self._main("关键词", "--window", "30", "--format", "json", "--no-log", "--no-update")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual([h["id"] for h in data["hits"]], ["条目新"])
        self.assertEqual(data["time_filter"]["undated_skipped"], 1)
        self.assertTrue(data["time_filter"]["since"])

    def test_since_until_bounds(self):
        since = (TODAY - datetime.timedelta(days=10)).isoformat()
        until = (TODAY - datetime.timedelta(days=100)).isoformat()
        rc, out = self._main("关键词", "--since", since, "--until", until,
                             "--format", "json", "--no-log", "--no-update")
        self.assertEqual(rc, 1)  # 新条目 ≥since 被排除，旧条目 >until 被排除 → 无命中
        rc, out = self._main("关键词", "--since", since, "--format", "json",
                             "--no-log", "--no-update")
        data = json.loads(out)
        self.assertEqual([h["id"] for h in data["hits"]], ["条目新"])

    def test_invalid_date_rejected(self):
        # argparse ap.error() 抛 SystemExit(2)，而非返回码
        with self.assertRaises(SystemExit) as cm:
            self._main("关键词", "--since", "not-a-date")
        self.assertEqual(cm.exception.code, 2)
        with self.assertRaises(SystemExit) as cm:
            self._main("关键词", "--window", "0")
        self.assertEqual(cm.exception.code, 2)

    def test_text_output_mentions_undated(self):
        rc, out = self._main("关键词", "--window", "30")
        self.assertIn("无时态", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
