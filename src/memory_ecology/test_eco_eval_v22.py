"""eco_eval v2.2.0 新判定线测试：伪 gold 构建 + INJ 注入超限 + GOLD top5 重放。

全部 fixture 隔离（tmp experiences），零 LLM（不测 ABS 维度）。
运行: python test_eco_eval_v22.py
"""
from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import eco_eval as ev
import eco_eval_gold as eg

ENTRY_A = """---
id: exp-test-0001
type: error
status: verified
last_hit: 2026-09-06
---
title: xarray 缺失时需激活 venv
action: 激活项目 venv 或安装 xarray
evidence: ModuleNotFoundError 复现
"""

ERR_LINE = "python failed: ModuleNotFoundError: No module named xarray"


def _mk_exp(root: Path) -> Path:
    exp = root / "experiences"
    exp.mkdir(parents=True)
    (exp / "exp-test-0001.md").write_text(ENTRY_A, encoding="utf-8")
    return exp


class GoldBuilderTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.exp = _mk_exp(Path(self._td.name))

    def tearDown(self):
        self._td.cleanup()

    def test_gold_from_hits_and_injections(self):
        (self.exp / ".injected.jsonl").write_text(
            json.dumps({"ts": "2026-09-06T10:00:00", "session": "s1", "error": ERR_LINE}) + "\n", encoding="utf-8")
        # R5 语义：归因取「早于 hit 的最近一次注入」——真实 hit 记录带 ts，夹具对齐
        (self.exp / ".hits.jsonl").write_text(
            json.dumps({"verdict": "hit", "entry_id": "exp-test-0001", "session": "s1",
                        "ts": "2026-09-06T10:05:00"}) + "\n" +
            json.dumps({"verdict": "hit", "entry_id": None, "session": "s1",
                        "ts": "2026-09-06T10:06:00"}) + "\n", encoding="utf-8")
        out = eg.build_gold(self.exp)
        self.assertEqual(len(out["gold"]), 1)  # entry_id null 的不进 gold
        g = out["gold"][0]
        self.assertEqual(g["entry_id"], "exp-test-0001")
        self.assertEqual(g["error"], ERR_LINE)

    def test_gold_attribution_ignores_later_injection(self):
        # R5：晚于 hit 的注入不得错配为该 hit 的重放查询
        (self.exp / ".injected.jsonl").write_text(
            json.dumps({"ts": "2026-09-06T10:00:00", "session": "s1", "error": "早的错误"}) + "\n" +
            json.dumps({"ts": "2026-09-06T12:00:00", "session": "s1", "error": ERR_LINE}) + "\n", encoding="utf-8")
        (self.exp / ".hits.jsonl").write_text(
            json.dumps({"verdict": "hit", "entry_id": "exp-test-0001", "session": "s1",
                        "ts": "2026-09-06T10:05:00"}) + "\n", encoding="utf-8")
        g = eg.build_gold(self.exp)["gold"][0]
        self.assertEqual(g["error"], "早的错误")

    def test_per_entry_cap(self):
        (self.exp / ".hits.jsonl").write_text(
            "".join(json.dumps({"verdict": "hit", "entry_id": "exp-test-0001", "session": f"s{i}"}) + "\n"
                    for i in range(5)), encoding="utf-8")
        out = eg.build_gold(self.exp, per_entry=3)
        self.assertEqual(len(out["gold"]), 3)


def null_id():
    return None


class InjLineTest(unittest.TestCase):
    def test_oversize_counting(self):
        p = Path(tempfile.gettempdir()) / f"_evtest_{id(self)}.jsonl"
        now = datetime.datetime.now().isoformat(timespec="seconds")
        old = (datetime.datetime.now() - datetime.timedelta(days=40)).isoformat(timespec="seconds")
        rows = [
            {"ts": now, "chars": 2000},            # 超限（30 天内）
            {"ts": now, "chars": 100},             # 正常
            {"ts": now, "chars": 1600},            # 超限
            {"ts": old, "chars": 9999},            # 40 天前，不计
            {"ts": now},                            # 无 chars，不计
        ]
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        try:
            oversized, measured = ev.inj_over_budget(ev._read_jsonl(p))
            self.assertEqual((oversized, measured), (2, 3))
        finally:
            p.unlink(missing_ok=True)


class GoldReplayTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        exp = _mk_exp(Path(self._td.name))
        import eco_note_error_query as errq
        self._errq = errq
        self._orig = errq.eq.EXP_DIR  # R12：存/恢复全局旋钮（对齐同仓既有模式）
        errq.eq.EXP_DIR = exp

    def tearDown(self):
        self._errq.eq.EXP_DIR = self._orig
        self._td.cleanup()

    def test_replay_hits_top5(self):
        ids = [h["path"] for h in self._errq.rank(ERR_LINE, top=5)]
        self.assertIn("exp-test-0001", ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
