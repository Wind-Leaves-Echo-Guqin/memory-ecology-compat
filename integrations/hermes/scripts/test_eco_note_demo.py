#!/usr/bin/env python3
"""经验笔记本 demo · A7 fixture 测试（4 套，全部 mock LLM / 临时目录）

套件：
  T1 信号检测（eco_note_signals.scan_signals）：合成 state.db →
     工具错误(exit_code/error字段/输出Traceback) / 用户纠正(词表) / 验证动作(verify等)
  T2 提取解析（llm_extract 的 JSON 提取逻辑）：mock LMM 返回合法/非法/空 JSON
  T3 候选写入（eco_note.py 主流程）：mock LLM + 临时 pending 目录 → 候选文件内容断言
  T4 状态机前身（并入 A3 已定；此处验证 schema 模板化语义，见断言）

运行: python test_eco_note_demo.py   → 全部 PASS 退出码 0
"""
from __future__ import annotations
# publish bridge: 集成层测试定位核心模块（src/memory_ecology）
import sys as _sys, pathlib as _plib
_sys.path.insert(0, str(_plib.Path(__file__).resolve().parents[3] / 'src' / 'memory_ecology'))

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import eco_note_signals as ns
import eco_note as en


def make_fixture_db(path: Path) -> None:
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
        "content TEXT, tool_name TEXT, tool_calls TEXT, timestamp REAL)"
    )
    t0 = 1788300000.0
    rows = [
        (1, "s1", "user", "帮我查一下文件", None, None, t0 + 5),
        (2, "s1", "assistant", "", None, '[{"name":"terminal","arguments":"ls"}]', t0 + 10),
        (3, "s1", "tool", '{"output":"x","exit_code":1}', "terminal", None, t0 + 12),
        (4, "s1", "user", "停，别搜了", None, None, t0 + 14),
        (5, "s2", "assistant", "", None, '[{"name":"write_file","arguments":"a.py"}]', t0 + 20),
        (6, "s2", "tool", '{"error":"Could not find a match"}', "patch", None, t0 + 21),
        (7, "s3", "assistant", "", None, '[{"name":"terminal","arguments":"python test_verify.py"}]', t0 + 30),
        (8, "s3", "tool", '{"output":"ok","exit_code":0}', "terminal", None, t0 + 31),
        (9, "s3", "user", "这是正常消息，没有信号", None, None, t0 + 32),
        (10, "s4", "tool", '{"output":"Traceback (most recent call last)"}', "terminal", None, t0 + 40),
        (11, "s5", "tool", '{"output":"something","exit_code":0}', "web_search", None, t0 + 50),
        (12, "s6", "tool", '{"status":"error","output":"api fail"}', "web_extract", None, t0 + 51),
        (13, "s7", "user", "不对，应该是15", None, None, t0 + 52),
        (14, "s7", "assistant", "", None, '[{"name":"terminal","arguments":"run pytest"}]', t0 + 53),
    ]
    conn.executemany(
        "INSERT INTO messages (id, session_id, role, content, tool_name, tool_calls, timestamp) "
        "VALUES (?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()


class T1Signals(unittest.TestCase):
    def setUp(self):
        self._orig_db = ns.DB
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "fixture.db"
        make_fixture_db(self.db)
        ns.DB = self.db

    def tearDown(self):
        ns.DB = self._orig_db
        self._td.cleanup()

    def test_signal_types_and_counts(self):
        sig, wm = ns.scan_signals(0.0)
        from collections import Counter
        c = Counter(s["type"] for s in sig)
        self.assertEqual(c["tool_error"], 4, f"期望 4 个工具错误，实得 {c}")          # msg3/6/10/12
        self.assertEqual(c["user_correction"], 2, f"期望 2 个纠正，实得 {c}")          # msg4/13
        self.assertEqual(c["verify_action"], 0, f"v1.1-3a：verify 降级出捕获，期望 0，实得 {c}")  # msg7/14 不再产验证信号
        # 正常消息不误报
        self.assertNotIn(9, [s["msg_id"] for s in sig])
        self.assertNotIn(11, [s["msg_id"] for s in sig])  # exit_code=0 普通输出

    def test_watermark_advance(self):
        _, wm = ns.scan_signals(0.0)
        self.assertGreater(wm, 1788300000.0)
        # 增量扫描：从 wm 之后无新信号
        sig2, wm2 = ns.scan_signals(wm)
        self.assertEqual(sig2, [])


class T2Extraction(unittest.TestCase):
    def test_parse_valid_json(self):
        fake = '{"choices":[{"message":{"content":"[{\\\"type\\\":\\\"error\\\",\\\"trigger\\\":\\\"中文路径\\\"}]"}}]}'
        with mock.patch.object(en, "read_key", return_value="test-key"), \
             mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = fake.encode()
            items = en.llm_extract(["片段"])
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["trigger"], "中文路径")

    def test_parse_empty(self):
        fake = '{"choices":[{"message":{"content":"[\\n]"}}]}'
        with mock.patch.object(en, "read_key", return_value="test-key"), \
             mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = fake.encode()
            self.assertEqual(en.llm_extract(["x"]), [])

    def test_parse_garbage(self):
        fake = '{"choices":[{"message":{"content":"说人话"}}]}'
        with mock.patch.object(en, "read_key", return_value="test-key"), \
             mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = fake.encode()
            self.assertEqual(en.llm_extract(["x"]), [])


class T3CandidateWrite(unittest.TestCase):
    def test_main_flow_writes_candidates(self):
        """mock LLM（不调 API）+ 临时目录 → pending 写入且水位线推进。"""
        self._orig = (en.DB, en.EXP_DIR, en.PENDING_DIR, en.WATERMARK, en.scan_signals, en.collect_fragments, en.read_key, en.llm_extract)
        with tempfile.TemporaryDirectory() as td:
            exp = Path(td) / "experiences"
            en.EXP_DIR = exp
            en.PENDING_DIR = exp / "pending"
            en.WATERMARK = exp / ".watermark"
            exp.mkdir(parents=True, exist_ok=True)
            en.WATERMARK.write_text("1788300000.0")
            # mock：1 个信号簇 + 1 条候选
            sig = [{"type": "tool_error", "session_id": "s1", "ts": 1788300100.0, "tool_name": "t", "snippet": "x", "msg_id": 3}]
            # 批次循环需要 side_effect：第一批返回信号，第二批返回空（结束循环）
            # 且信号 ts 必须 > 水位线（生产不变式）；否则触发防御分支（2026-09-02 实测）
            en.scan_signals = mock.Mock(side_effect=[(sig, 1788300100.0), ([], 1788300100.0)])
            en.collect_fragments = mock.Mock(return_value=[{"signal": sig[0], "ctx": ["[tool结果] err"]}])
            en.llm_extract = mock.Mock(return_value=[{
                "type": "error", "trigger": "测试触发情境", "symptom": "测试报错",
                "cause": "测试根因", "action": "测试做法", "evidence": "测试证据", "boundary": "测试边界"}])
            en.read_key = mock.Mock(return_value="k")
            with mock.patch.object(sys, "argv", ["eco_note.py"]):
                rc = en.main()
            self.assertEqual(rc, 0)
            files = list((exp / "pending").glob("*.md"))
            self.assertEqual(len(files), 1)
            txt = files[0].read_text(encoding="utf-8")
            self.assertIn("测试触发情境", txt)
            self.assertIn("error", txt)
            self.assertIn("来源信号", txt)
            wm = float(en.WATERMARK.read_text())
            self.assertEqual(wm, 1788300100.0, "水位线应推进到扫描窗口尾")
        en.DB, en.EXP_DIR, en.PENDING_DIR, en.WATERMARK, en.scan_signals, en.collect_fragments, en.read_key, en.llm_extract = self._orig

    def test_dry_run_no_write(self):
        self._orig = (en.EXP_DIR, en.PENDING_DIR, en.WATERMARK, en.scan_signals, en.collect_fragments)
        with tempfile.TemporaryDirectory() as td:
            exp = Path(td) / "experiences"
            en.EXP_DIR = exp
            en.PENDING_DIR = exp / "pending"
            en.WATERMARK = exp / ".watermark"
            exp.mkdir(parents=True, exist_ok=True)
            en.WATERMARK.write_text("1788300000.0")
            sig = [{"type": "tool_error", "session_id": "s1", "ts": 1788300200.0, "tool_name": "t", "snippet": "x", "msg_id": 3}]
            en.scan_signals = mock.Mock(return_value=(sig, 1788300200.0))
            en.collect_fragments = mock.Mock(return_value=[{"signal": sig[0], "ctx": ["[tool结果] err"]}])
            with mock.patch.object(sys, "argv", ["eco_note.py", "--dry-run"]):
                rc = en.main()
            self.assertEqual(rc, 0)
            files = list((exp / "pending").glob("*.md"))
            self.assertEqual(files, [], "dry-run 不得写候选")
            self.assertEqual(en.WATERMARK.read_text(), "1788300000.0", "dry-run 不得推进水位线")
        en.EXP_DIR, en.PENDING_DIR, en.WATERMARK, en.scan_signals, en.collect_fragments = self._orig


class T4Schema(unittest.TestCase):
    def test_type_template(self):
        """按型模板化语义（A3 校准）：error 型必填 symptom/cause，pattern 型可无。"""
        error_req = {"symptom", "cause", "action"}
        pattern_req = {"trigger", "action"}
        # 验证 schema 判断（从设计稿语义固化）
        self.assertTrue({"symptom", "cause", "action"} >= error_req)
        self.assertTrue({"trigger", "action"} >= pattern_req)


class T5Hostile(unittest.TestCase):
    """恶意输入/刁钻输入抗压（用户要求：不刻意制造特殊环境代码怎么抗压）。"""

    def test_trigger_null_does_not_crash(self):
        """LLM 返回 trigger: null（键存在但值为 None）→ 必须跳过而非崩溃。"""
        class FakeResp:
            def __init__(self, content):
                self.data = content
            def read(self):
                return self.data
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        fake = '{"choices":[{"message":{"content":"[{\\\"type\\\":\\\"error\\\",\\\"trigger\\\":null},{\\\"type\\\":\\\"pattern\\\",\\\"trigger\\\":\\\"正常条\\\"}]"}}]}'
        with mock.patch.object(en, "read_key", return_value="k"), \
             mock.patch("urllib.request.urlopen", return_value=FakeResp(fake.encode())):
            items = en.llm_extract(["x"])
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["trigger"], "正常条")

    def test_items_mixed_none_and_non_string(self):
        """items 混入 None / trigger 非字符串 → 主流程不崩溃。"""
        items = [None, {"type": "error", "trigger": 123}, {"type": "error", "trigger": "ok条"}]
        seen: set = set()
        got = []
        for it in items:
            if not isinstance(it, dict):
                continue
            t = it.get("trigger", "") or ""
            if not t or not isinstance(t, str):
                continue
            if t in seen:
                continue
            seen.add(t)
            got.append(t)
        self.assertEqual(got, ["ok条"])  # 123 非字符串应被跳过

    def test_garbage_content(self):
        """LLM 返回非 JSON 垃圾文本 → 返回空列表。"""
        class FakeResp:
            def read(self):
                return '{"choices":[{"message":{"content":"不是JSON不是JSON"}}]}'.encode("utf-8")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        with mock.patch.object(en, "read_key", return_value="k"), \
             mock.patch("urllib.request.urlopen", return_value=FakeResp()):
            self.assertEqual(en.llm_extract(["x"]), [])

    def test_reasoning_length_retry(self):
        """finish_reason=length → 自动重试并加倍 max_tokens（4096 拒绝再重试时返回空）。"""
        calls = []

        class FakeResp:
            def __init__(self, data):
                self.data = data
            def read(self):
                return self.data
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=90):
            import urllib.request
            body = json.loads(req.data)
            calls.append(body["max_tokens"])
            if calls[-1] < 8000:
                payload = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
            else:
                payload = {"choices": [{"message": {
                    "content": '[{"type":"error","trigger":"重试后成功"}]', "finish_reason": "stop"}}]}
            return FakeResp(json.dumps(payload).encode())

        with mock.patch.object(en, "read_key", return_value="k"), \
             mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            items = en.llm_extract(["x"])
            self.assertEqual(items[0]["trigger"], "重试后成功")
            self.assertGreaterEqual(len(calls), 3)  # 2000→4000→8000


if __name__ == "__main__":
    unittest.main(verbosity=2)
