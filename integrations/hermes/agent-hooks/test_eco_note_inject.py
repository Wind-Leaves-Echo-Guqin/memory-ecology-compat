#!/usr/bin/env python3
"""经验笔记本 · 报错注入 hook 测试（L1，2026-09-03）

覆盖：注入触发 / 静默条件（无错/冷却/非深挖） / 三态判定（hit 回写 last_hit、miss 记事件） /
防回声（经验参考标记不误判纠正） / 坏 payload fail-open。

运行: python test_eco_note_inject.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import eco_note_inject as ei

GOOD_ENTRY = """---
id: exp-20260902-0001
type: error
status: verified
last_hit: 2026-09-01
---
title: xarray 缺失时需激活 venv
action: 激活项目 venv 或安装 xarray
boundary: venv 内也会缺则重装
evidence: ModuleNotFoundError 复现
"""


def make_db(path: Path, now: float):
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
        "content TEXT, tool_name TEXT, tool_calls TEXT, timestamp REAL)"
    )
    rows = [
        (1, "s1", "user", "帮我处理数据", None, None, now - 300),
        (2, "s1", "tool", '{"output":"ModuleNotFoundError: No module named xarray","exit_code":1}',
         "terminal", None, now - 60),
        (3, "s2", "tool", '{"output":"ok","exit_code":0}', "terminal", None, now - 60),
    ]
    conn.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


class InjectTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "state.db"
        self.exp = Path(self._td.name) / "experiences"
        self.exp.mkdir()
        self.now = 1788393480.0  # = 注入事件(1788393600)前 120s：错误消息在 10min 窗口内、且早于注入事件
        make_db(self.db, self.now)
        (self.exp / "exp-20260902-0001.md").write_text(GOOD_ENTRY, encoding="utf-8")
        os.environ["ECO_NOTE_INJECT_TESTING"] = "1"
        os.environ["ECO_NOTE_INJECT_DB"] = str(self.db)
        os.environ["ECO_NOTE_INJECT_EXP_DIR"] = str(self.exp)
        os.environ["ECO_NOTE_INJECT_NOW"] = "2026-09-03T00:00:00+00:00"
        os.environ["ECO_NOTE_INJECT_LOG"] = str(Path(self._td.name) / "inject.log")

    def tearDown(self):
        for k in ("ECO_NOTE_INJECT_TESTING", "ECO_NOTE_INJECT_DB", "ECO_NOTE_INJECT_EXP_DIR",
                  "ECO_NOTE_INJECT_NOW", "ECO_NOTE_INJECT_LOG"):
            os.environ.pop(k, None)
        self._td.cleanup()

    def _payload(self, session="s1", model="deepseek-v4-flash"):
        return {"extra": {"session_id": session, "model": model, "user_message": "继续"}}

    def test_inject_on_error(self):
        out = ei.handle(self._payload())
        d = json.loads(out)
        self.assertIn("context", d)
        self.assertIn("经验参考", d["context"])
        self.assertIn("exp-20260902-0001", d["context"])
        events = ei.read_events(".injected.jsonl")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["session"], "s1")
        # v2.2.0（R13）：chars（INJ 判定线数据源）与 error（GOLD 重放查询）字段必须存在
        self.assertIn("chars", events[0])
        self.assertGreater(events[0]["chars"], 0)
        self.assertIn("error", events[0])
        # v2.2.0 压缩格式：注入文本仍含标题与做法片段（回归保护）
        self.assertIn("xarray 缺失时需激活 venv", d["context"])
        self.assertIn("激活项目 venv", d["context"])

    def test_silent_without_error(self):
        out = ei.handle(self._payload(session="s2"))
        self.assertEqual(json.loads(out), {})

    def test_silent_cooling(self):
        ei.handle(self._payload())
        out = ei.handle(self._payload())  # 冷却期内
        self.assertEqual(json.loads(out), {})

    def test_silent_non_deepseek(self):
        out = ei.handle(self._payload(model="gpt-4o"))
        self.assertEqual(json.loads(out), {})

    def test_three_state_hit(self):
        ei.handle(self._payload())
        # 模拟注入后 agent 干活（无新错误 + 一次工具调用）
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
            (10, "s1", "assistant", "", None, '[{"name":"terminal","arguments":"activate venv"}]', 1788393700),
        )
        conn.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
            (11, "s1", "tool", '{"output":"ok","exit_code":0}', "terminal", None, 1788393710),
        )
        conn.commit()
        conn.close()
        os.environ["ECO_NOTE_INJECT_NOW"] = "2026-09-03T00:02:00+00:00"
        ei.handle(self._payload())  # 下轮 hook → 先判定
        hits = ei.read_events(".hits.jsonl")
        self.assertEqual(hits[-1]["verdict"], "hit")
        txt = (self.exp / "exp-20260902-0001.md").read_text(encoding="utf-8")
        # last_hit 更新（ISO 自 now 覆盖值取日期）
        self.assertRegex(txt, r"last_hit: \d{4}-\d{2}-\d{2}")

    def test_three_state_miss(self):
        ei.handle(self._payload())
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
            (20, "s1", "tool", '{"output":"again failed","exit_code":1}', "terminal", None, 1788393700),
        )
        conn.commit()
        conn.close()
        os.environ["ECO_NOTE_INJECT_NOW"] = "2026-09-03T00:02:00+00:00"
        ei.handle(self._payload())
        hits = ei.read_events(".hits.jsonl")
        self.assertEqual(hits[-1]["verdict"], "miss")
        self.assertEqual(hits[-1]["reason"], "new-tool-error")

    def test_echo_mark_not_correction(self):
        # 用户消息带经验参考标记 → 不算纠正（防回声）
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
            (30, "s1", "user", "【经验参考(只读参考, 可忽略) 最近出错…停", None, None, self.now + 10),
        )
        conn.commit()
        conn.close()
        os.environ["ECO_NOTE_INJECT_NOW"] = "2026-09-03T00:02:00+00:00"
        ei.handle(self._payload())
        hits = ei.read_events(".hits.jsonl")
        # 最后一条不应是 user-correction miss（该信息被回声排除）
        self.assertNotEqual(hits[-1].get("reason"), "user-correction") if hits else None

    def test_bad_payload_fail_open(self):
        self.assertEqual(ei.handle(None), "{}")
        self.assertEqual(ei.handle("not-a-dict"), "{}")

    def test_auto_verify_after_two_hits(self):
        # Q1（2026-09-06）：质量门——confirmed hit ≥2 次自动 draft→verified
        draft = GOOD_ENTRY.replace("status: verified", "status: draft")
        (self.exp / "exp-20260902-0002.md").write_text(draft, encoding="utf-8")
        for _ in range(2):
            ei.append_event(".hits.jsonl", {"verdict": "hit", "entry_id": "exp-20260902-0002"})
        self.assertTrue(ei.maybe_auto_verify("exp-20260902-0002"))
        txt = (self.exp / "exp-20260902-0002.md").read_text(encoding="utf-8")
        self.assertIn("status: verified", txt)
        # 不足阈值不升
        (self.exp / "exp-20260902-0003.md").write_text(draft, encoding="utf-8")
        ei.append_event(".hits.jsonl", {"verdict": "hit", "entry_id": "exp-20260902-0003"})
        self.assertFalse(ei.maybe_auto_verify("exp-20260902-0003"))
        self.assertIn("status: draft", (self.exp / "exp-20260902-0003.md").read_text(encoding="utf-8"))

    def test_shadow_decision_recorded_keep(self):
        # 影子三态判定（PORT_SPEC §6，shadow 先行）：注入行为不变 + .inject_decisions.jsonl 记账
        out = ei.handle(self._payload())
        d = json.loads(out)
        self.assertIn("context", d)  # 注入主流程不受影子影响
        self.assertEqual(len(ei.read_events(".injected.jsonl")), 1)
        decs = ei.read_events(".inject_decisions.jsonl")
        self.assertEqual(len(decs), 1)
        rec = decs[0]
        # 统一 schema（inject_gate.format_jsonl）+ session 附加字段
        for k in ("ts", "unit_id", "decision", "confidence", "reason", "signals", "session"):
            self.assertIn(k, rec)
        self.assertEqual(rec["unit_id"], "exp-20260902-0001")
        self.assertEqual(rec["session"], "s1")
        # 现状如此：verified 条目 → keep/high（verified 永不 drop）
        self.assertEqual(rec["decision"], "keep")
        self.assertEqual(rec["confidence"], "high")

    def test_shadow_stale_draft_dropped(self):
        # 现状如此：experiences 域唯一的可触发 drop 路径 = age>180d 且 status=draft
        #（id 日期 exp-20260301 → NOW 2026-09-03 = 186 天）
        stale = GOOD_ENTRY.replace("exp-20260902-0001", "exp-20260301-0001") \
                          .replace("status: verified", "status: draft")
        (self.exp / "exp-20260301-0001.md").write_text(stale, encoding="utf-8")
        # 现状如此（钉住）：qy.search 的同 episode 折叠把无 source 条目的空串 episode
        # 视为同组——同关键词命中的两条无 source 条目只留字典序第一条。单条目沙盒规避。
        (self.exp / "exp-20260902-0001.md").unlink()
        out = ei.handle(self._payload())
        self.assertIn("context", json.loads(out))  # 影子模式下 drop 条目仍被注入（行为不变）
        decs = ei.read_events(".inject_decisions.jsonl")
        self.assertEqual(len(decs), 1)
        self.assertEqual(decs[0]["unit_id"], "exp-20260301-0001")
        self.assertEqual(decs[0]["decision"], "drop")
        self.assertIn("stale_draft", decs[0]["signals"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
