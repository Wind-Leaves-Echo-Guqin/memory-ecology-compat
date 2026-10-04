#!/usr/bin/env python3
"""批 3 需求链路回归（P0.5，2026-10-04）

1. memory_query 打分：名字命中 > 正文命中；active 优于 dormant；归档降权
2. 命中回写：last_hit=今天、use_count+1；缺行自动补
3. 遥测 JSONL 与复活提案文件落盘（--no-log 关闭路径）
4. 门④ 使用驱动休眠：窗口内有命中 → 免于降级；命中过旧 → 照常降级
5. revive：dormant 原地激活；archived 移回 detail 激活；账本留痕

隔离：全部写临时目录（模块级路径属性 patch）。运行: python test_p05_demand.py
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

import eco_review as er
import memory_query as mq
from lib.memstore import parse_frontmatter_strict

TODAY = datetime.date.today()


def _entry_md(slug: str, body: str, status: str = "active", mtype: str = "semantic",
              last_hit: str | None = None, use_count: int | None = None,
              last_verified: str = "2026-09-01") -> str:
    lines = ["---", f"name: {slug}", f"type: {mtype}", f"status: {status}",
             "occurrences: 3", "session_count: 2",
             f"last_verified: {last_verified}", "last_seen: 2026-09-01"]
    if last_hit:
        lines.append(f"last_hit: {last_hit}")
    if use_count is not None:
        lines.append(f"use_count: {use_count}")
    lines += ["---", "", body, ""]
    return "\n".join(lines)


class MemoryQueryBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.detail = self.root / "detail"
        self.archive = self.root / "archive"
        self.detail.mkdir(parents=True)
        self.archive.mkdir(parents=True)
        self.hits_log = self.root / ".memory_hits.jsonl"
        self.gate_log = self.root / "gate_log"
        self.db = self.root / "eco.db"

    def tearDown(self):
        self._td.cleanup()

    def _write(self, dirpath: Path, slug: str, text: str) -> Path:
        p = dirpath / f"{slug}.md"
        p.write_text(text, encoding="utf-8")
        return p


class TestScoring(MemoryQueryBase):
    def test_name_hit_beats_body_hit(self):
        self._write(self.detail, "缓存策略要点", _entry_md("缓存策略要点", "正文中提到部署相关内容"))
        self._write(self.detail, "部署脚本清单", _entry_md("部署脚本清单", "正文中顺带提到缓存策略四个字"))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        scored = [(mq.score_entry(["缓存"], e), e) for e in entries]
        scored = [(s, e) for s, e in scored if s is not None]
        scored.sort(key=lambda t: (-t[0], t[1]["slug"]))
        self.assertIn("缓存策略要点", scored[0][1]["slug"])

    def test_active_beats_dormant(self):
        self._write(self.detail, "条目甲", _entry_md("条目甲", "相同正文关键词", status="dormant"))
        self._write(self.detail, "条目乙", _entry_md("条目乙", "相同正文关键词", status="active"))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        scored = sorted([(mq.score_entry(["关键词"], e), e["slug"]) for e in entries],
                        key=lambda x: -x[0])
        self.assertEqual(scored[0][1], "条目乙")

    def test_archive_downgraded(self):
        self._write(self.detail, "条目甲", _entry_md("条目甲", "唯一关键词正文"))
        self._write(self.archive, "条目乙", _entry_md("条目乙", "唯一关键词正文", status="archived"))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        self.assertEqual(len(entries), 2)
        s_detail = mq.score_entry(["唯一关键词正文"], next(e for e in entries if e["source"] == "detail"))
        s_archive = mq.score_entry(["唯一关键词正文"], next(e for e in entries if e["source"] == "archive"))
        self.assertGreater(s_detail, s_archive)

    def test_multi_term_and_semantics(self):
        self._write(self.detail, "条目甲", _entry_md("条目甲", "包含部署的内容"))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        self.assertIsNone(mq.score_entry(["部署", "迁移"], entries[0]))  # 缺一个 term → 不命中
        self.assertIsNotNone(mq.score_entry(["部署", "内容"], entries[0]))


class TestHitWriteback(MemoryQueryBase):
    def test_bump_hit_updates_fields(self):
        p = self._write(self.detail, "目标条目",
                        _entry_md("目标条目", "正文", last_hit="2026-08-01", use_count=2))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        self.assertTrue(mq.bump_hit(entries[0]))
        fields, _ = parse_frontmatter_strict(p.read_text(encoding="utf-8"))
        self.assertEqual(fields["last_hit"], TODAY.isoformat())
        self.assertEqual(fields["use_count"], "3")
        self.assertEqual(p.read_text(encoding="utf-8").count("last_hit:"), 1)

    def test_bump_hit_inserts_missing_lines(self):
        p = self._write(self.detail, "裸条目", _entry_md("裸条目", "正文"))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        self.assertTrue(mq.bump_hit(entries[0]))
        fields, _ = parse_frontmatter_strict(p.read_text(encoding="utf-8"))
        self.assertEqual(fields["last_hit"], TODAY.isoformat())
        self.assertEqual(fields["use_count"], "1")

    def test_telemetry_and_proposal(self):
        self._write(self.detail, "休眠条目", _entry_md("休眠条目", "正文", status="dormant"))
        entries = mq.scan_memory_dirs(self.detail, self.archive)
        hits = [{"score": 1, **e} for e in entries]
        with mock.patch.object(mq, "HITS_LOG", self.hits_log), \
             mock.patch.object(mq, "GATE_LOG_DIR", self.gate_log):
            mq.log_hits("测试词", hits)
            proposed = mq.propose_revive(hits)
        self.assertTrue(self.hits_log.exists())
        rec = json.loads(self.hits_log.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(rec["query"], "测试词")
        self.assertEqual(rec["hits"], ["休眠条目"])
        self.assertEqual(proposed, 1)
        rf = self.gate_log / f"revive-{TODAY.isoformat()}.md"
        self.assertTrue(rf.exists())
        self.assertIn("休眠条目", rf.read_text(encoding="utf-8"))
        # 重复提案去重
        with mock.patch.object(mq, "GATE_LOG_DIR", self.gate_log):
            self.assertEqual(mq.propose_revive(hits), 0)


class TestUseDrivenDormancy(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.detail = Path(self._td.name) / "detail"
        self.detail.mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()

    def _scan(self) -> list[dict]:
        actions, files, failures = er.scan_detail(self.detail)
        return actions

    def test_recent_hit_exempts_from_dormancy(self):
        recent = (TODAY - datetime.timedelta(days=2)).isoformat()
        (self.detail / "被使用的旧条目.md").write_text(
            _entry_md("被使用的旧条目", "正文", last_verified="2026-01-01", last_hit=recent),
            encoding="utf-8")
        actions = self._scan()
        self.assertEqual(actions, [], "窗口内有命中 → 不降级")

    def test_old_hit_still_expires(self):
        old = (TODAY - datetime.timedelta(days=200)).isoformat()
        (self.detail / "从未再被使用的条目.md").write_text(
            _entry_md("从未再被使用的条目", "正文", last_verified="2026-01-01", last_hit=old),
            encoding="utf-8")
        actions = self._scan()
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"][0], "mark_dormant")

    def test_no_hit_field_keeps_calendar_rule(self):
        (self.detail / "无命中字段条目.md").write_text(
            _entry_md("无命中字段条目", "正文", last_verified="2026-01-01"),
            encoding="utf-8")
        actions = self._scan()
        self.assertEqual(len(actions), 1)


class TestRevive(MemoryQueryBase):
    def test_revive_dormant_in_place(self):
        p = self._write(self.detail, "休眠待复活", _entry_md("休眠待复活", "正文", status="dormant"))
        ok, msg = mq.revive("休眠待复活", self.detail, self.archive, self.db)
        self.assertTrue(ok, msg)
        fields, _ = parse_frontmatter_strict(p.read_text(encoding="utf-8"))
        self.assertEqual(fields["status"], "active")
        self.assertEqual(fields["last_verified"], TODAY.isoformat())

    def test_revive_archived_moves_back(self):
        self._write(self.archive, "归档待复活", _entry_md("归档待复活", "正文", status="archived"))
        ok, msg = mq.revive("归档待复活", self.detail, self.archive, self.db)
        self.assertTrue(ok, msg)
        self.assertFalse(list(self.archive.rglob("归档待复活*.md")))
        restored = list(self.detail.glob("归档待复活*.md"))
        self.assertEqual(len(restored), 1)
        fields, _ = parse_frontmatter_strict(restored[0].read_text(encoding="utf-8"))
        self.assertEqual(fields["status"], "active")

    def test_revive_missing_slug_fails_cleanly(self):
        ok, msg = mq.revive("不存在的条目", self.detail, self.archive, self.db)
        self.assertFalse(ok)

    def test_revive_logs_to_ledger(self):
        import sqlite3
        self._write(self.detail, "留痕条目", _entry_md("留痕条目", "正文", status="dormant"))
        mq.revive("留痕条目", self.detail, self.archive, self.db)
        conn = sqlite3.connect(str(self.db))
        try:
            row = conn.execute(
                "SELECT action, slug FROM review_log WHERE action='revive' AND slug='留痕条目'").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
