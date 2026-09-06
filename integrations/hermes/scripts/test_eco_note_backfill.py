#!/usr/bin/env python3
"""eco_note_backfill 测试（2026-09-03）：dry-run 不写 / 候选落 backfill 子目录 / 上限生效。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import eco_note_backfill as bf
import eco_note as en
import eco_note_signals as sig


def make_db(path: Path):
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
        "content TEXT, tool_name TEXT, tool_calls TEXT, timestamp REAL)"
    )
    rows = []
    t0 = 1788390000.0
    for i in range(1, 9):  # s1 8 条错误
        rows.append((i, "s1", "tool", '{"output":"err","exit_code":1}', "terminal", None, t0 + i))
    for i in range(9, 13):  # s2 4 条错误
        rows.append((i, "s2", "tool", '{"output":"err2","exit_code":1}', "patch", None, t0 + i))
    rows.append((50, "s3", "tool", '{"output":"ok","exit_code":0}', "terminal", None, t0 + 50))
    conn.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "state.db"
        make_db(self.db)
        en.DB = self.db
        en.EXP_DIR = Path(self._td.name) / "experiences"
        en.PENDING_DIR = en.EXP_DIR / "pending"

    def tearDown(self):
        self._td.cleanup()

    def _run(self, argv, llm_items):
        with mock.patch.object(sys, "argv", ["eco_note_backfill.py"] + argv), \
             mock.patch.object(bf, "session_errors", side_effect=None) as _:
            pass  # 不改 session_errors；直接构造调用

    def test_collect_clusters(self):
        cs = bf.collect_clusters("s1", [1, 2, 3, 50, 51], window=12)
        self.assertEqual(len(cs), 2)  # [1,2,3] 与 [50,51] 两组

    def test_top_sessions_order(self):
        tops = bf.top_sessions(2)
        self.assertEqual(tops[0], "s1")
        self.assertEqual(tops[1], "s2")

    def test_dry_run_no_write(self):
        with mock.patch.object(sys, "argv", ["eco_note_backfill.py", "--sessions", "s1", "--dry-run"]), \
             mock.patch.object(en, "fetch_context", return_value=["[tool结果] err"]), \
             mock.patch.object(en, "llm_extract", return_value=[{"type": "error", "trigger": "T1"}]):
            rc = bf.main()
        self.assertEqual(rc, 0)
        self.assertFalse((en.PENDING_DIR / "backfill").exists())

    def test_write_to_backfill_subdir(self):
        with mock.patch.object(sys, "argv", ["eco_note_backfill.py", "--sessions", "s1", "--max-cands", "5"]), \
             mock.patch.object(en, "fetch_context", return_value=["[tool结果] err"]), \
             mock.patch.object(en, "llm_extract", return_value=[{"type": "error", "trigger": "T1"},
                                                                {"type": "negative", "trigger": "T2"}]):
            rc = bf.main()
        self.assertEqual(rc, 0)
        files = list((en.PENDING_DIR / "backfill").glob("*.md"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertIn("**T1**", text)
        self.assertIn("backfill / session s1", text)  # 来源标记


if __name__ == "__main__":
    unittest.main(verbosity=2)
# publish bridge: 集成层测试定位核心模块（src/memory_ecology）
import sys as _sys, pathlib as _plib
_sys.path.insert(0, str(_plib.Path(__file__).resolve().parents[3] / 'src' / 'memory_ecology'))
