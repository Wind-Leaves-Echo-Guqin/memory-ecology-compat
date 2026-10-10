#!/usr/bin/env python3
"""S3 证据链回归（v2.3.0，2026-10-07）

1. add_entry：evidence 字段落盘（显式 evidence 优先，回退 origin_session_id）
2. 门① CONFLICT 端到端：新条目 evidence 指向被取代旧条目（与旧条目 superseded_by 互指），
   旧条目入隔离区且 distilled_at 清空
3. distill_stage._stamp_distilled_at：候选落盘即在源条目登记蒸馏状态位
4. memory_query.revive：复活清空 distilled_at（源复活 → 待重蒸馏可逆闭环）
5. memory_query json 输出携带 evidence/distilled_at

隔离：全部写临时目录（模块属性 patch / 显式路径参数）。运行: python test_evidence_chain.py
"""
from __future__ import annotations

import argparse
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

import write_gate as wg
import distill_stage as ds
import memory_query as mq
from lib.memstore import parse_frontmatter

TODAY_ISO = datetime.date.today().isoformat()


def _detail_md(slug: str, body: str, status: str = "active",
               distilled_at: str = "") -> str:
    lines = ["---", f"name: {slug}", "type: semantic", f"status: {status}",
             "occurrences: 1", "session_count: 1",
             f"first_seen: {TODAY_ISO}", f"last_seen: {TODAY_ISO}",
             f"last_hit: {TODAY_ISO}", f"valid_time: {TODAY_ISO}",
             f"transaction_time: {TODAY_ISO}T00:00:00",
             f"last_verified: {TODAY_ISO}", "origin_session_id: ",
             "superseded_by: "]
    if distilled_at:
        lines.append(f"distilled_at: {distilled_at}")
    lines += ["---", "", body, ""]
    return "\n".join(lines)


class EvidenceBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.detail = self.root / "detail"
        self.quarantine = self.root / "quarantine"
        self.pending = self.root / "pending"
        self.detail.mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()


class TestAddEntryEvidence(EvidenceBase):
    def test_explicit_evidence_recorded(self):
        p = wg.add_entry(self.detail, "现在全面使用 Vue 3",
                         {"type": "semantic", "evidence": "旧条目-slug"})
        fm, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        self.assertEqual(fm["evidence"], "旧条目-slug")
        self.assertEqual(fm["distilled_at"], "")

    def test_evidence_falls_back_to_session(self):
        p = wg.add_entry(self.detail, "另一条记忆内容",
                         {"type": "semantic", "origin_session_id": "sess-abc"})
        fm, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        self.assertEqual(fm["evidence"], "sess-abc")

    def test_plain_add_empty_evidence(self):
        p = wg.add_entry(self.detail, "第三条记忆内容", {"type": "semantic"})
        fm, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        self.assertEqual(fm["evidence"], "")


class TestConflictChainEndToEnd(EvidenceBase):
    def test_conflict_links_new_to_old(self):
        # 旧条目在 detail，且已被蒸馏过（distilled_at 有值）
        (self.detail / "旧框架选择.md").write_text(
            _detail_md("旧框架选择", "项目主框架是 React 18", distilled_at="2026-10-01"),
            encoding="utf-8")
        self.pending.mkdir()
        (self.pending / "dsh-20261007.md").write_text(
            "- [fact] 项目主框架已迁移到 Vue 3，不再使用 React\n", encoding="utf-8")
        decision = [{"idx": 0, "type": "semantic", "action": "CONFLICT",
                     "target": "旧框架选择", "note": "矛盾",
                     "reason": "之前主框架是 React，现已迁移到 Vue 3"}]
        args = argparse.Namespace(dry_run=False, pending=self.pending, detail=self.detail,
                                  db=self.root / "eco.db", logdir=self.root / "gate_log")
        with mock.patch.object(wg, "llm_decision", return_value=decision), \
             mock.patch.object(wg, "QUARANTINE_DIR", self.quarantine), \
             redirect_stdout(io.StringIO()):
            rc = wg._run(args)
        self.assertEqual(rc, 0)
        # 旧条目进隔离区，superseded_by 指向新条目，distilled_at 清空
        qfiles = list(self.quarantine.rglob("旧框架选择.md"))
        self.assertEqual(len(qfiles), 1)
        fm_old, _ = parse_frontmatter(qfiles[0].read_text(encoding="utf-8"))
        self.assertEqual(fm_old["status"], "superseded")
        self.assertEqual(fm_old["superseded_reason"], "之前主框架是 React，现已迁移到 Vue 3")
        self.assertEqual(fm_old["distilled_at"], "")
        # 新条目在 detail，evidence 反向指向旧条目（互指闭环）
        new_files = [p for p in self.detail.glob("*.md")]
        self.assertEqual(len(new_files), 1)
        fm_new, _ = parse_frontmatter(new_files[0].read_text(encoding="utf-8"))
        self.assertEqual(fm_new["evidence"], "旧框架选择")
        self.assertNotEqual(fm_new["slug"] if "slug" in fm_new else new_files[0].stem,
                            "旧框架选择")


class TestDistillStamp(EvidenceBase):
    def test_stamp_writes_distilled_at(self):
        p = self.detail / "稳定事实.md"
        p.write_text(_detail_md("稳定事实", "用户偏好暗色主题"), encoding="utf-8")
        d = {"path": p, "name": "稳定事实", "fm": {}, "body": "用户偏好暗色主题"}
        ds._stamp_distilled_at(d, TODAY_ISO)
        fm, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        self.assertEqual(fm["distilled_at"], TODAY_ISO)
        self.assertEqual(d["fm"]["distilled_at"], TODAY_ISO)

    def test_stamp_missing_file_silent(self):
        d = {"path": self.detail / "不存在.md", "name": "不存在", "fm": {}, "body": "x"}
        ds._stamp_distilled_at(d, TODAY_ISO)  # 不抛异常即通过


class TestReviveClearsDistilled(EvidenceBase):
    def test_revive_clears_distilled_at(self):
        p = self.detail / "休眠条目.md"
        p.write_text(_detail_md("休眠条目", "某条休眠记忆", status="dormant",
                                distilled_at="2026-09-01"), encoding="utf-8")
        db = self.root / "eco.db"
        ok, msg = mq.revive("休眠条目", detail_dir=self.detail,
                            archive_dir=self.root / "archive", db=db)
        self.assertTrue(ok, msg)
        fm, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
        self.assertEqual(fm["status"], "active")
        self.assertEqual(fm["distilled_at"], "")

    def test_json_hits_carry_evidence(self):
        (self.detail / "证据条目.md").write_text(
            _detail_md("证据条目", "独特关键词甲乙丙", distilled_at="2026-09-09"),
            encoding="utf-8")
        (self.detail / "证据条目.md").write_text(
            _detail_md("证据条目", "独特关键词甲乙丙").replace("superseded_by: ",
                                                              "evidence: 来源条目\nsuperseded_by: "),
            encoding="utf-8")
        buf = io.StringIO()
        with mock.patch.object(mq, "DETAIL_DIR", self.detail), \
             mock.patch.object(mq, "ARCHIVE_DIR", self.root / "archive"), \
             mock.patch.object(mq, "HITS_LOG", self.root / "h.jsonl"), \
             redirect_stdout(buf):
            rc = mq.main(["独特关键词", "--format", "json", "--no-log", "--no-update"])
        self.assertEqual(rc, 0)
        h = json.loads(buf.getvalue())["hits"][0]
        self.assertEqual(h["evidence"], "来源条目")
        self.assertEqual(h["distilled_at"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
