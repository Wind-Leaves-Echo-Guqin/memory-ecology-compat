#!/usr/bin/env python3
"""生态四门持久化回归测试（v2.1.1 ⑦，2026-09-05）

背景：v2.0 四门 fixture 曾是 ad-hoc 验证脚本（跑完即弃）——生产树四门零持久化测试
（调研证据链抓出的真缺口）。本套件将四门核心行为固化为常驻回归：
- write_gate：L2 写入 / LLM 决策（mock）/ 规则兜底
- eco_quota：提升候选条件 / safe_only 挤出保护 / 字符数口径（纯规则）
- distill_stage：备份/替换（原子）/ LLM trait（mock JSON）
- eco_review：过期阈值 / status 重写+复核刷新 / quarantine 扫描（纯规则）

隔离：patch lib.config._ENV_OVERRIDE → 临时生态根；零写生产。
运行: python test_eco_gates.py
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

import lib.config as libcfg
import lib.llm as lllm
import write_gate as wg
import eco_quota as eq_
import distill_stage as ds
import eco_review as er


class GateTestBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._orig_override = libcfg._ENV_OVERRIDE
        libcfg._ENV_OVERRIDE = self._td.name
        self.root = Path(self._td.name)

    def tearDown(self):
        libcfg._ENV_OVERRIDE = self._orig_override
        self._td.cleanup()

    def _mkdir(self, *parts: str) -> Path:
        p = self.root.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p


class TestWriteGate(GateTestBase):
    def test_add_entry_creates_l2_file(self):
        d = self._mkdir("memories", "detail")
        p = wg.add_entry(d, "这是一条新记忆内容", {"type": "semantic"})
        self.assertTrue(p.exists())
        text = p.read_text(encoding="utf-8")
        self.assertIn("type: semantic", text)
        self.assertIn("这是一条新记忆内容", text)

    def test_llm_decision_mocked(self):
        # llm_decision 提取 [.*] + 要求含 idx 键
        with mock.patch.object(lllm, "complete", return_value=json.dumps(
            [{"idx": 0, "action": "ADD", "text": "模拟新增", "reason": "t"}], ensure_ascii=False)):
            out = wg.llm_decision("测试上下文")
            self.assertEqual(out[0]["action"], "ADD")

    def test_rule_type_deterministic(self):
        # 规则兜底：LLM 不可用时类型初判——只断言不崩且返回合法类型
        self.assertIn(wg.rule_type("用户喜欢简洁的界面风格"), ("behavior", "semantic", "preference"))


class TestQuota(GateTestBase):
    def _fm(self, **kw):
        fm = {"type": "semantic", "status": "active", "occurrences": "3",
              "session_count": "2", "origin_session_id": "", "transaction_time": ""}
        fm.update(kw)
        return {"fm": fm}

    def test_promo_candidate_condition(self):
        self.assertTrue(eq_.is_promo_candidate(self._fm()))
        self.assertFalse(eq_.is_promo_candidate(self._fm(occurrences="2")))   # occ 不足
        self.assertFalse(eq_.is_promo_candidate(self._fm(status="dormant")))  # 状态不符
        self.assertFalse(eq_.is_promo_candidate(self._fm(type="episodic")))   # 类型不符

    def test_pick_evict_safe_only_protects_high_value(self):
        # meta=(text, is_promoted_this_run, orig_index)；safe_only=True 排除高价值
        meta = [("低价值条目甲", False, 0), ("高价值条目乙", False, 1)]
        r = eq_.pick_evict(meta, set(), {"高价值条目乙"}, safe_only=True)
        self.assertIsNotNone(r)  # 有可挤出项
        self.assertEqual(r[0], 0)  # 挤出的是低价值（索引 0），高价值被保护

    def test_chars_of_discipline(self):
        self.assertEqual(eq_.chars_of(["123", "456"]), len("123\n§\n456\n"))
        self.assertEqual(eq_.chars_of([]), 0)


class TestDistill(GateTestBase):
    def test_backup_before_append(self):
        uf = self._mkdir("memories") / "USER.md"
        uf.write_text("原有内容\n", encoding="utf-8")
        bak = ds._backup_user(uf)
        self.assertIsNotNone(bak)
        self.assertTrue(bak.exists())
        self.assertIn("原有", bak.read_text(encoding="utf-8"))

    def test_replace_user_entry(self):
        uf = self._mkdir("memories") / "USER.md"
        uf.write_text("旧行为规则\n", encoding="utf-8")
        ok = ds._replace_user_entry(uf, "旧行为规则", "新行为规则")
        self.assertTrue(ok)
        out = uf.read_text(encoding="utf-8")
        self.assertIn("新行为规则", out)
        self.assertNotIn("旧行为规则", out)

    def test_llm_trait_mocked_json(self):
        # llm_trait 提取 {...}.trait（mock 需返回 JSON 对象）
        with mock.patch.object(lllm, "complete", return_value='{"trait": "喜欢蓝白配色界面"}'):
            trait = ds.llm_trait("用户说喜欢蓝白配色的界面")
            self.assertIn("蓝白", trait)


class TestReview(GateTestBase):
    def test_expiry_thresholds(self):
        self.assertEqual(er.expiry_threshold("semantic"), 90)
        self.assertEqual(er.expiry_threshold("episodic"), 30)

    def test_rewrite_status_and_refresh(self):
        raw = "---\nstatus: dormant\nlast_verified: 2026-08-01\n---\n正文"
        out = er.rewrite_status(raw, "archived", refresh_lv=True)
        self.assertIn("status: archived", out)
        # Q29 修复（2026-09-06）：原写死 2026-09-05，日期一变整套件恒红（日期炸弹）
        self.assertIn(f"last_verified: {datetime.date.today().isoformat()}", out)  # 刷新为今天（替换已有行）
        # refresh_lv=False 时不碰 last_verified
        out2 = er.rewrite_status(raw, "archived")
        self.assertIn("last_verified: 2026-08-01", out2)

    def test_mark_consumed_done(self):
        # Q22（2026-09-06）：消费标记——pending 只进不出会被误读为未消费
        pending = self.root / "pending"
        pending.mkdir(parents=True, exist_ok=True)
        (pending / "a.md").write_text("- [fact] 测试候选", encoding="utf-8")
        (pending / "b.md.done.md").write_text("- [fact] 已消费", encoding="utf-8")
        (pending / "README.md").write_text("说明", encoding="utf-8")
        n = wg.mark_consumed(pending)
        self.assertEqual(n, 1)
        self.assertFalse((pending / "a.md").exists())
        self.assertTrue((pending / "a.md.done.md").exists())
        self.assertTrue((pending / "README.md").exists())  # README 不动

    def test_scan_quarantine_returns_tuple(self):
        q = self._mkdir("memories", "quarantine")
        f = q / "quar-2026-08-01.md"
        f.write_text("---\nid: q1\n---\n", encoding="utf-8")
        res = er.scan_quarantine(q)
        self.assertIsInstance(res, tuple)  # (actions, failures)
        self.assertEqual(res[0], [])  # 未超 90 天 → 无清理候选
        self.assertEqual(res[1], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
