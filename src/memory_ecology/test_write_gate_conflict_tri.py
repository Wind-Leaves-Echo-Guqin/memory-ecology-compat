#!/usr/bin/env python3
"""S2 矛盾三态与失效理由回归（v2.3.0，2026-10-07）

1. supersede_entry 写入 superseded_reason/superseded_at/superseded_by 并移入隔离区
2. 无理由调用 → superseded_reason 空串（不缺字段）
3. llm_decision 透传 LLM 的 reason 键（mock complete）
4. memory_query 命中 superseded：text 输出已失效行、json 带理由与取代者、
   复活提案表带失效原因
5. safeio：superseded_at 参与日期格式校验（非法日期拒绝写入）

隔离：全部写临时目录。运行: python test_write_gate_conflict_tri.py
"""
from __future__ import annotations

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
import memory_query as mq
from lib import safeio
from lib.memstore import dump_frontmatter, parse_frontmatter_strict

TODAY_ISO = __import__("datetime").date.today().isoformat()


def _detail_md(slug: str, body: str, status: str = "active") -> str:
    return "\n".join(["---", f"name: {slug}", "type: semantic", f"status: {status}",
                      "occurrences: 1", "session_count: 1",
                      f"first_seen: {TODAY_ISO}", f"last_seen: {TODAY_ISO}",
                      f"last_hit: {TODAY_ISO}", f"valid_time: {TODAY_ISO}",
                      f"transaction_time: {TODAY_ISO}T00:00:00",
                      f"last_verified: {TODAY_ISO}", "origin_session_id: ",
                      "superseded_by: ", "---", "", body, ""])


class SupersedeTestBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.detail = self.root / "detail"
        self.quarantine = self.root / "quarantine"
        self.detail.mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()

    def _entry(self, slug: str, body: str = "旧事实正文", status: str = "active"):
        p = self.detail / f"{slug}.md"
        p.write_text(_detail_md(slug, body, status), encoding="utf-8")
        fields, _ = parse_frontmatter_strict(p.read_text(encoding="utf-8"))
        return {"path": p, "fm": fields, "body": body, "name": slug}


class TestSupersedeEntry(SupersedeTestBase):
    def test_reason_recorded_and_moved(self):
        d = self._entry("旧条目")
        target = wg.supersede_entry(d, "新条目", self.quarantine, reason="之前用 React，现已迁移到 Vue")
        self.assertFalse(d["path"].exists())
        self.assertTrue(target.exists())
        self.assertIn(str(TODAY_ISO), str(target.parent))  # 隔离区日期子目录
        fm, _ = parse_frontmatter_strict(target.read_text(encoding="utf-8"))
        self.assertEqual(fm["status"], "superseded")
        self.assertEqual(fm["superseded_by"], "新条目")
        self.assertEqual(fm["superseded_at"], TODAY_ISO)
        self.assertEqual(fm["superseded_reason"], "之前用 React，现已迁移到 Vue")

    def test_no_reason_defaults_empty(self):
        d = self._entry("旧条目乙")
        target = wg.supersede_entry(d, "新条目乙", self.quarantine)
        fm, _ = parse_frontmatter_strict(target.read_text(encoding="utf-8"))
        self.assertEqual(fm["superseded_reason"], "")
        self.assertEqual(fm["superseded_at"], TODAY_ISO)

    def test_duplicate_name_collides(self):
        d1 = self._entry("同名条目", body="甲版本正文")
        wg.supersede_entry(d1, "新条", self.quarantine, reason="r1")
        d2 = self._entry("同名条目", body="乙版本正文")
        target = wg.supersede_entry(d2, "新条", self.quarantine, reason="r2")
        self.assertTrue(target.exists())
        self.assertNotEqual(target.name, "同名条目.md")  # 重名加 md5 后缀


class TestLlmDecisionReason(SupersedeTestBase):
    def test_reason_key_passthrough(self):
        raw = json.dumps([{"idx": 1, "type": "semantic", "action": "CONFLICT",
                           "target": "旧条目", "note": "矛盾",
                           "reason": "之前用 React，现已迁移到 Vue"}], ensure_ascii=False)
        with mock.patch.object(wg._llm, "complete", return_value=raw):
            decisions = wg.llm_decision("候选 1: [semantic] 现在用 Vue")
        self.assertEqual(decisions[0]["reason"], "之前用 React，现已迁移到 Vue")

    def test_prompt_demands_reason(self):
        self.assertIn("reason", wg.PROMPT)
        self.assertIn("CONFLICT", wg.PROMPT)


class TestQuerySideSuperseded(SupersedeTestBase):
    def _write_superseded(self):
        text = _detail_md("已失效条目", "旧世界的事实", status="superseded")
        # 直接以失效态落盘（模拟门①产出），并带理由
        text = text.replace("superseded_by: ", "superseded_by: 新条目")
        lines = text.splitlines()
        idx = lines.index("---", 1)
        lines.insert(idx, "superseded_at: " + TODAY_ISO)
        lines.insert(idx, "superseded_reason: 之前用 React，现已迁移到 Vue")
        (self.detail / "已失效条目.md").write_text("\n".join(lines), encoding="utf-8")

    def test_text_output_shows_reason(self):
        self._write_superseded()
        buf = io.StringIO()
        with mock.patch.object(mq, "DETAIL_DIR", self.detail), \
             mock.patch.object(mq, "ARCHIVE_DIR", self.root / "archive"), \
             mock.patch.object(mq, "HITS_LOG", self.root / "hits.jsonl"), \
             redirect_stdout(buf):
            rc = mq.main(["旧世界", "--no-log", "--no-update"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("已失效", out)
        self.assertIn("React", out)
        self.assertIn("新条目", out)

    def test_json_output_carries_reason(self):
        self._write_superseded()
        buf = io.StringIO()
        with mock.patch.object(mq, "DETAIL_DIR", self.detail), \
             mock.patch.object(mq, "ARCHIVE_DIR", self.root / "archive"), \
             mock.patch.object(mq, "HITS_LOG", self.root / "hits.jsonl"), \
             redirect_stdout(buf):
            rc = mq.main(["旧世界", "--format", "json", "--no-log", "--no-update"])
        self.assertEqual(rc, 0)
        hits = json.loads(buf.getvalue())["hits"]
        self.assertEqual(hits[0]["superseded_reason"], "之前用 React，现已迁移到 Vue")
        self.assertEqual(hits[0]["superseded_by"], "新条目")
        self.assertEqual(hits[0]["superseded_at"], TODAY_ISO)

    def test_revive_proposal_includes_reason(self):
        self._write_superseded()
        entries = mq.scan_memory_dirs(self.detail, self.root / "archive")
        hits = [{"score": 1, **e} for e in entries]
        gate_log = self.root / "gate_log"
        with mock.patch.object(mq, "GATE_LOG_DIR", gate_log):
            proposed = mq.propose_revive(hits)
        self.assertEqual(proposed, 1)
        row = (gate_log / f"revive-{mq.TODAY.isoformat()}").with_suffix(".md").read_text(encoding="utf-8")
        self.assertIn("失效原因", row)
        self.assertIn("React", row)


class TestSafeioSupersededAt(unittest.TestCase):
    def test_superseded_at_validated_as_date(self):
        bad = _detail_md("条目", "正文").replace("superseded_by: ", "superseded_at: 明天\nsuperseded_by: ")
        with self.assertRaises(ValueError):
            safeio.write_entry(Path(tempfile.gettempdir()) / "never-written.md", bad, kind="detail")

    def test_valid_superseded_at_passes(self):
        errs = safeio.validate_detail_fm({"superseded_at": "2026-10-07", "superseded_reason": "r"})
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
