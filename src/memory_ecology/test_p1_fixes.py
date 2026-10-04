#!/usr/bin/env python3
"""批 2 P1 修复回归（2026-10-04）

1. 门① add_entry：已存在条目读/写失败必须抛出（不再假成功记账）
2. 门① update_entry：同轮多候选命中同一目标 → 逐次从磁盘重读（occurrences 不丢更新）
3. parse_candidates：任务 checkbox 前缀剥离（"- [ ] [fact] x" / "- [] y"）
4. 门② has_src 只认 active detail（dormant 副本不满足免写 → 挤出必落盘）
5. 门③ dry-run 不调 LLM；evolve 的 *.md.approved 候选豁免观察期且排最前
6. adopt：同文件重复 trigger 去重；写成功后标记失败不再误报拒纳
7. eco_eval._date_of：无日期返回 None（不虚增近期样本）

隔离：临时生态根；零写生产。运行: python test_p1_fixes.py
"""
from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import lib.config as libcfg
import lib.llm as lllm
import eco_quota as eq_
import distill_stage as ds
import eco_eval as ev
import write_gate as wg
from lib import memstore
from lib import safeio

_TF = tempfile.TemporaryDirectory()
libcfg._ENV_OVERRIDE = _TF.name  # 门① 真实路径用例：无 .env → LLM 走规则兜底，零网络


class TestWriteGateP1(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.detail = Path(self._td.name) / "detail"
        self.detail.mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()

    def test_add_entry_raises_on_write_failure(self):
        """已存在条目 + 写失败 → 异常上抛（候选不消费），不再假成功。"""
        p = wg.add_entry(self.detail, "已有条目内容甲", {"type": "semantic"})
        self.assertTrue(p.exists())
        with mock.patch.object(safeio, "write_entry", side_effect=OSError("磁盘满")):
            with self.assertRaises(OSError):
                wg.add_entry(self.detail, "已有条目内容甲", {"type": "semantic"})

    def test_update_entry_rereads_from_disk(self):
        """同轮两次 UPDATE 同一目标：occurrences 应 +2（磁盘重读），快照口径只 +1。"""
        p = wg.add_entry(self.detail, "近似重复目标条目", {"type": "semantic"})
        import lib.memstore as ms
        snapshot = {"path": p, "fm": ms.parse_frontmatter(p.read_text(encoding="utf-8"))[0],
                    "body": "近似重复目标条目", "name": p.stem}
        wg.update_entry(snapshot)
        wg.update_entry(snapshot)  # 同一陈旧快照再调一次
        fm, _ = ms.parse_frontmatter(p.read_text(encoding="utf-8"))
        self.assertEqual(fm["occurrences"], "3")

    def test_parse_candidates_strips_task_checkbox(self):
        pending = Path(self._td.name) / "pending"
        pending.mkdir(parents=True)
        (pending / "mix.md").write_text(
            "- [ ] [fact] 带任务标记的类型行\n"
            "- [] 无类型行也该成为候选\n"
            "- [x] [lesson] 完成态标记行\n"
            "普通散文行（保持旧语义：跳过）\n",
            encoding="utf-8")
        cands = memstore.parse_candidates(pending)
        by_text = {c["text"]: c["ctype"] for c in cands}
        self.assertEqual(by_text.get("带任务标记的类型行"), "fact")
        self.assertEqual(by_text.get("无类型行也该成为候选"), "")
        self.assertEqual(by_text.get("完成态标记行"), "lesson")
        self.assertNotIn("普通散文行（保持旧语义：跳过）", by_text)


class TestQuotaHasSrcActiveOnly(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._td.cleanup()

    def test_dormant_twin_does_not_satisfy_write(self):
        """L1 条目仅与 dormant detail 同源 → 挤出时必须写入 detail（旧版跳写=active 副本消失）。"""
        root = Path(self._td.name)
        detail = root / "detail"
        detail.mkdir(parents=True)
        twin_body = "同源条目的正文内容，用于匹配 dormant 副本"
        (detail / "dormant-one.md").write_text(
            f"---\ntype: semantic\nstatus: dormant\noccurrences: 9\nsession_count: 9\n---\n{twin_body}",
            encoding="utf-8")
        detail_items = eq_.load_detail(detail)
        prefixes = eq_.detail_prefixes(detail_items)       # dormant 不进前缀集
        qprefixes = eq_.qualified_prefixes(detail_items)
        filler = "填充条目" + "长" * 60
        l1 = root / "MEMORY.md"
        l1.write_text(f"{filler}\n§\n{twin_body}\n", encoding="utf-8")  # twin 在末尾（LRU 先挤出）
        actions, final_text = eq_.process_file(l1, 60, detail_items, prefixes, qprefixes,
                                               allow_promo=False)  # 60*0.85=51 < 92 → 触发挤出
        self.assertIsNotNone(actions)
        twin_actions = [a for a in actions if a["text"] == twin_body]
        self.assertTrue(twin_actions, "twin 条目应被挤出")
        self.assertTrue(twin_actions[0]["write_detail"],
                        "dormant 副本不算 L2 已存 → 必须落盘 detail")


class TestDistillP1(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        libcfg._ENV_OVERRIDE = str(self.root)

    def tearDown(self):
        self._td.cleanup()

    def _args(self, dry: bool):
        import argparse
        return argparse.Namespace(
            dry_run=dry, detail=self.root / "memories" / "detail",
            cand=self.root / "memories" / "user_candidates",
            user=self.root / "memories" / "USER.md",
            quarantine=self.root / "memories" / "quarantine",
            db=self.root / "eco.db")

    def test_dry_run_never_calls_llm(self):
        cand = self.root / "memories" / "user_candidates"
        cand.mkdir(parents=True)
        (cand / "观察中的候选.md").write_text(
            f"---\nsource: src-one\ncreated: 2026-01-01\nstatus: observing\n---\n某条稳定特质",
            encoding="utf-8")
        with mock.patch.object(lllm, "complete", side_effect=AssertionError("dry-run 禁网")):
            ds._run(self._args(dry=True))  # 不抛即通过（旧版 dry-run 会真实调 LLM）

    def test_approved_candidate_bypasses_observation(self):
        """evolve approve 产出 *.md.approved：人工已确认 → 豁免 30 天观察期，且排最前。"""
        import io
        from contextlib import redirect_stdout
        detail = self.root / "memories" / "detail"
        detail.mkdir(parents=True)
        # 活跃源条目（否则 stage-1 的源失效检查会 reject）
        (detail / "src-a.md").write_text("---\ntype: semantic\nstatus: active\n---\n刚创建的普通候选",
                                         encoding="utf-8")
        (detail / "src-b.md").write_text("---\ntype: semantic\nstatus: active\n---\n人工确认的特质",
                                         encoding="utf-8")
        cand = self.root / "memories" / "user_candidates"
        cand.mkdir(parents=True)
        (cand / "普通候选.md").write_text(
            f"---\nsource: src-a\ncreated: 2026-10-01\nstatus: observing\n---\n刚创建的普通候选",
            encoding="utf-8")
        (cand / "人工确认候选.md.approved").write_text(
            f"---\nsource: src-b\ncreated: 2026-10-01\nstatus: observing\n---\n人工确认的特质",
            encoding="utf-8")
        cands = ds.load_candidates(cand)
        self.assertTrue(cands[0]["approved"])                       # approved 排最前
        self.assertEqual(cands[0]["body"], "人工确认的特质")
        buf = io.StringIO()
        with redirect_stdout(buf):
            ds._run(self._args(dry=True))
        report = buf.getvalue().splitlines()
        promoted = [r for r in report if "PROMOTE" in r or "REPLACE" in r]
        self.assertTrue(any("人工确认" in r for r in promoted),
                        f"approved 候选应豁免观察期被晋升: {promoted}")
        self.assertFalse(any("刚创建的普通候选" in r and ("PROMOTE" in r or "REPLACE" in r)
                             for r in report),
                         "未满观察期的普通候选不应被晋升")


class TestAdoptP1(unittest.TestCase):
    """adopt 依赖模块级路径常量——reload 到临时生态根再测。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / "experiences" / "pending").mkdir(parents=True)
        (self.root / "experiences").mkdir(parents=True, exist_ok=True)
        self._orig = libcfg._ENV_OVERRIDE
        libcfg._ENV_OVERRIDE = str(self.root)
        import eco_note_adopt as ad
        self.ad = importlib.reload(ad)

    def tearDown(self):
        libcfg._ENV_OVERRIDE = self._orig
        self._td.cleanup()

    def test_duplicate_trigger_within_file_skipped(self):
        pending = self.root / "experiences" / "pending" / "2026-10-01.md"
        pending.write_text(
            "- [error] **同一条教训**\n    - symptom: 症状A\n    - cause: 根因A\n"
            "- [error] **同一条教训**\n    - symptom: 症状B\n    - cause: 根因B\n",
            encoding="utf-8")
        rc = self.ad.main()
        self.assertEqual(rc, 0)
        exps = list((self.root / "experiences").glob("exp-*.md"))
        self.assertEqual(len(exps), 1, "同文件重复 trigger 只接纳一次")

    def test_mark_failure_not_reported_as_rejected(self):
        pending = self.root / "experiences" / "pending" / "2026-10-02.md"
        pending.write_text("- [error] **标记会失败的条目**\n    - symptom: 症状\n    - cause: 根因\n",
                          encoding="utf-8")
        with mock.patch.object(self.ad, "mark_accepted", side_effect=OSError("标记失败")):
            rc = self.ad.main()
        self.assertEqual(rc, 0)
        exps = list((self.root / "experiences").glob("exp-*.md"))
        self.assertEqual(len(exps), 1, "条目已写入：标记失败 ≠ 拒纳")


class TestEvalDateOf(unittest.TestCase):
    def test_undated_name_returns_none(self):
        self.assertIsNone(ev._date_of("gate-无日期.md"))
        self.assertIsNone(ev._date_of("gate-2026-13-99.md"))  # 非法日期
        self.assertEqual(ev._date_of("gate-2026-10-04.md").isoformat(), "2026-10-04")


if __name__ == "__main__":
    unittest.main(verbosity=2)
