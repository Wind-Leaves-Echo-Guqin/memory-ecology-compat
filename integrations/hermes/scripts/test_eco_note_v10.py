#!/usr/bin/env python3
"""经验笔记本 · 正式版 v1.0 测试套件（四件套冒烟+恶意输入）

覆盖：eco_note / eco_note_signals / eco_note_adopt / eco_note_index
全部 mock LLM / 临时目录；生产文件只读。

运行: python test_eco_note_v10.py
"""
from __future__ import annotations
# publish bridge: 集成层测试定位核心模块（src/memory_ecology）
import sys as _sys, pathlib as _plib
_sys.path.insert(0, str(_plib.Path(__file__).resolve().parents[3] / 'src' / 'memory_ecology'))

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
import eco_note as en
import eco_note_signals as ns
import eco_note_adopt as ea
import eco_note_index as ei
import eco_note_query as qy
import eco_note_error_query as eq_
import eco_note_verify as ev
import eco_note_backfill as bf
import eco_note_backfill_runall as rn
import eco_note_merge_list as ml


def make_db(path: Path):
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
        "content TEXT, tool_name TEXT, tool_calls TEXT, timestamp REAL)"
    )
    t0 = 1788300000.0
    rows = [
        (1, "s1", "user", "帮我查一下文件", None, None, t0 + 5),
        (2, "s1", "tool", '{"output":"x","exit_code":1}', "terminal", None, t0 + 12),
        (3, "s1", "user", "停，别搜了", None, None, t0 + 14),
        (4, "s2", "tool", '{"error":"Could not find a match"}', "patch", None, t0 + 21),
    ]
    conn.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


class T0MakeId(unittest.TestCase):
    """v1.1-6（2026-09-02）：稳定 id 生成断言——同 trigger 二次运行结果一致、序号递增、避开既有 id。"""

    def test_stable_and_increment(self):
        existing = {"exp-20260902-0236", "exp-20260902-0574"}
        first = ea.make_id(existing, "2026-09-02", "同一条 trigger")
        second = ea.make_id(existing, "2026-09-02", "同一条 trigger")
        self.assertEqual(first, second)  # 确定性：同输入同输出
        self.assertEqual(first, "exp-20260902-0001")  # 0001/0002 未被占用 → 从 0001 起
        nxt = ea.make_id(existing | {first}, "2026-09-02", "别的 trigger")
        self.assertEqual(nxt, "exp-20260902-0002")  # 序号递增

    def test_skip_used_numbers(self):
        existing = {"exp-20260902-0001", "exp-20260902-0002", "exp-20260902-9999"}
        eid = ea.make_id(existing, "2026-09-02", "x")
        self.assertEqual(eid, "exp-20260902-0003")  # 跳过 0001/0002，9999 不参与（只跳最小可用）
        # 旧 hash 格式（数字后缀）也被视为占用：0236 存在 → 不会生成 0236
        self.assertNotIn(eid, existing)

    def test_date_isolation(self):
        existing = {"exp-20260902-0001"}
        eid = ea.make_id(existing, "2026-09-03", "x")
        self.assertEqual(eid, "exp-20260903-0001")  # 跨日不互相挤占


class T0Query(unittest.TestCase):
    """v1.1-8（2026-09-02）：检索 + 同 episode 折叠断言。"""

    def setUp(self):
        self._orig = qy.EXP_DIR
        self._td = tempfile.TemporaryDirectory()
        qy.EXP_DIR = Path(self._td.name)
        self._write("exp-20260902-0001.md", "s1 | tool_error", "netCDF 处理报错")
        self._write("exp-20260902-0002.md", "s1 | tool_error", "也是 netCDF 的坑（同源）")
        self._write("exp-20260902-0003.md", "s2 | user_correction", "换个 netCDF 话题")

    def tearDown(self):
        qy.EXP_DIR = self._orig
        self._td.cleanup()

    def _write(self, name: str, source: str, body: str):
        (qy.EXP_DIR / name).write_text(
            f"---\nid: {name[:-3]}\ntype: error\nstatus: draft\nsource: {source}\n---\n\ntitle: {body}\n", encoding="utf-8")

    def test_hit_and_fold(self):
        hits = qy.search("netCDF", top=3)
        stems = [h["path"] for h in hits]
        self.assertEqual(len(hits), 2)  # 0002 与 0001 同 source → 折叠
        self.assertIn("exp-20260902-0001", stems)
        self.assertIn("exp-20260902-0003", stems)

    def test_no_hit(self):
        self.assertEqual(qy.search("不存在词xyz"), [])

    def test_norm_case(self):
        hits = qy.search("NETCDF", top=3)
        self.assertEqual(len(hits), 2)  # 大小写归一化


class T0ErrQuery(unittest.TestCase):
    """v1.1-1a（2026-09-02）：关键词提取 + 报错→经验聚合断言。"""

    def setUp(self):
        self._orig = qy.EXP_DIR
        self._td = tempfile.TemporaryDirectory()
        qy.EXP_DIR = Path(self._td.name)

    def tearDown(self):
        qy.EXP_DIR = self._orig
        self._td.cleanup()

    def _write(self, name: str, body: str):
        (qy.EXP_DIR / name).write_text(
            f"---\nid: {name[:-3]}\ntype: error\nstatus: draft\nsource: s1 | tool_error\n---\n\ntitle: {body}\n", encoding="utf-8")

    def test_keyword_extract(self):
        kws = eq_.extract_keywords("Traceback: xarray not installed, ModuleNotFoundError in preprocess_xarray.py")
        self.assertIn("xarray", kws)
        self.assertIn("ModuleNotFoundError", kws)
        self.assertNotIn("Traceback", kws)  # 停用词剔除

    def test_rank_hit(self):
        self._write("exp-20260902-0001.md", "xarray 缺失时报 ModuleNotFoundError")
        self._write("exp-20260902-0002.md", "别的坑")
        hits = eq_.rank("ModuleNotFoundError: xarray 未安装", top=3)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["path"], "exp-20260902-0001")

    def test_no_keyword(self):
        self.assertEqual(eq_.extract_keywords("啊"), [])
        self.assertEqual(eq_.rank("啊", top=3), [])

    def test_rank_episode_fold(self):
        # ⑤ v2.1.1：同 episode（source）不同条目 → 聚合后只留分数最高的一条
        self._write("exp-20260902-0001.md", "xarray 缺失时报 ModuleNotFoundError")
        self._write("exp-20260902-0002.md", "同一事件的另一条 xarray 经验")
        hits = eq_.rank("ModuleNotFoundError xarray", top=3)
        self.assertEqual(len(hits), 1)  # 两条同 source → 折叠为 1
        self.assertEqual(hits[0]["path"], "exp-20260902-0001")


class T0Parse(unittest.TestCase):
    """v1.1-5（2026-09-02）：pending 行解析——旧/新格式兼容、长证据不截断。"""

    def _pending(self, content: str) -> Path:
        p = Path(tempfile.mkdtemp()) / "p.md"
        p.write_text(content, encoding="utf-8")
        return p

    def test_legacy_boundary_split(self):
        # v1.0 旧格式：evidence 与 boundary 同行（| 分隔）
        cands = ea.parse_pending_md(self._pending(
            "- [error] **x**\n    - evidence: 长证据A | boundary: 边界B\n    - 来源信号: tool_error / session s1\n"))
        self.assertEqual(cands[0]["fields"]["evidence"], "长证据A")
        self.assertEqual(cands[0]["fields"]["boundary"], "边界B")

    def test_new_format_separate_lines(self):
        cands = ea.parse_pending_md(self._pending(
            "- [error] **x**\n    - evidence: 长证据A\n    - boundary: 边界B\n"))
        self.assertEqual(cands[0]["fields"]["evidence"], "长证据A")
        self.assertEqual(cands[0]["fields"]["boundary"], "边界B")

    def test_long_field_kept(self):
        long_ev = "E" * 300
        cands = ea.parse_pending_md(self._pending(f"- [error] **x**\n    - evidence: {long_ev}\n"))
        self.assertEqual(len(cands[0]["fields"]["evidence"]), 300)  # 无截断


class T0Verify(unittest.TestCase):
    """v1.1-3b（2026-09-02）：证据登记命令断言——draft→verified、last_verified/last_hit 更新、幂等拒绝。"""

    def setUp(self):
        self._orig = ev.EXP_DIR
        self._td = tempfile.TemporaryDirectory()
        ev.EXP_DIR = Path(self._td.name)

    def tearDown(self):
        ev.EXP_DIR = self._orig
        self._td.cleanup()

    def _entry(self, status="draft", evidence="旧证据"):
        p = ev.EXP_DIR / "exp-20260902-0236.md"
        p.write_text(
            f"---\nid: exp-20260902-0236\ntype: error\nstatus: {status}\ncreated: 2026-09-02T00:00:00\n"
            f"last_hit: 2026-09-02\n---\n\ntitle: 某个坑\nevidence: {evidence}\n", encoding="utf-8")
        return p

    def test_verify_mark(self):
        self._entry()
        ok, msg = ev.verify_entry(ev.EXP_DIR / "exp-20260902-0236.md")
        self.assertTrue(ok)
        txt = (ev.EXP_DIR / "exp-20260902-0236.md").read_text(encoding="utf-8")
        self.assertIn("status: verified", txt)
        self.assertIn("last_verified:", txt)
        self.assertRegex(txt, r"last_hit: \d{4}-\d{2}-\d{2}")
        baks = list(ev.EXP_DIR.glob("*.bak-verify-*"))
        self.assertEqual(len(baks), 1)
        self.assertIn("status: draft", baks[0].read_text(encoding="utf-8"))  # 备份保留原文

    def test_verify_evidence_replaces(self):
        self._entry()
        ok, _ = ev.verify_entry(ev.EXP_DIR / "exp-20260902-0236.md", evidence="复现命令验证成功")
        txt = (ev.EXP_DIR / "exp-20260902-0236.md").read_text(encoding="utf-8")
        self.assertIn("evidence: 复现命令验证成功", txt)
        self.assertNotIn("evidence: 旧证据", txt)

    def test_verify_idempotent(self):
        self._entry(status="verified")
        ok, msg = ev.verify_entry(ev.EXP_DIR / "exp-20260902-0236.md")
        self.assertFalse(ok)
        self.assertIn("已是 verified", msg)

    def test_find_entry_short_tail(self):
        self._entry()
        hit = ev.find_entry(ev.EXP_DIR, "0236")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.name, "exp-20260902-0236.md")
        self.assertIsNone(ev.find_entry(ev.EXP_DIR, "9999"))


class T0Usage(unittest.TestCase):
    """v1.1-7a（2026-09-02）：usage 记账断言。"""

    def setUp(self):
        self._orig = en.USAGE_LOG
        self._td = tempfile.TemporaryDirectory()
        en.USAGE_LOG = Path(self._td.name) / ".usage.jsonl"

    def tearDown(self):
        en.USAGE_LOG = self._orig
        self._td.cleanup()

    def test_log_written(self):
        en.log_usage({"usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})
        lines = en.USAGE_LOG.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["total_tokens"], 120)

    def test_no_usage_skipped(self):
        en.log_usage({"choices": []})
        self.assertFalse(en.USAGE_LOG.exists())  # 无 usage 字段不落账（旧响应容错）


class T0Echo(unittest.TestCase):
    """L1 防回声（2026-09-03）：提取侧剔除含经验注入标记的上下文行。"""

    def test_strip_echo_lines(self):
        ctx = ["[用户] 帮我处理", "【经验参考(只读参考, 可忽略) 最近出错…", "[tool结果] ok"]
        out = en.strip_echo_lines(ctx)
        self.assertEqual(len(out), 2)
        self.assertNotIn("【经验参考", "".join(out))

    def test_no_mark_kept(self):
        ctx = ["[agent 调用] terminal", "[用户] 正常"]
        self.assertEqual(en.strip_echo_lines(ctx), ctx)


class T0RunallLock(unittest.TestCase):
    """② v2.1.1（2026-09-04）：队列锁断言——双活拒绝 / stale 回收 / 释放后重拿。"""

    def setUp(self):
        self._orig = (en.EXP_DIR, en.PENDING_DIR, bf.BACKFILL_SUBDIR)
        self._td = tempfile.TemporaryDirectory()
        en.EXP_DIR = Path(self._td.name) / "experiences"
        en.PENDING_DIR = en.EXP_DIR / "pending"
        bf.BACKFILL_SUBDIR = "backfill"

    def tearDown(self):
        en.EXP_DIR, en.PENDING_DIR, bf.BACKFILL_SUBDIR = self._orig
        self._td.cleanup()

    def test_acquire_then_blocked(self):
        lock = rn.acquire_lock()
        self.assertIsNotNone(lock)
        # 模拟第二执行器：锁已存在且 pid 存活（本进程）→ 拒绝
        self.assertIsNone(rn.acquire_lock())
        lock.unlink()

    def test_stale_lock_reclaimed(self):
        bf.backfill_dir().mkdir(parents=True, exist_ok=True)
        (bf.backfill_dir() / ".runall.lock").write_text("999999" + "9" * 6, encoding="utf-8")  # 死 pid
        lock = rn.acquire_lock()
        self.assertIsNotNone(lock)  # stale 被回收
        lock.unlink()

    def test_release_then_reacquire(self):
        lock = rn.acquire_lock()
        lock.unlink()
        lock2 = rn.acquire_lock()
        self.assertIsNotNone(lock2)  # 释放后可重拿
        lock2.unlink()


class T0MergeList(unittest.TestCase):
    """⑥ v2.1.1（2026-09-05）：合并清单生成器断言——近似对检出/无关不报/建议保留者。"""

    def test_find_pairs(self):
        items = [
            {"id": "exp-A", "title": "用 glob（如 *.NC）扫描目录做批处理时", "zone": "正式层"},
            {"id": "exp-B", "title": "用 glob（如 *.NC）扫描目录做批处理时", "zone": "候选"},
            {"id": "exp-C", "title": "在默认 Python 环境中直接运行数据检查脚本处理 NetCDF 数据", "zone": "正式层"},
            {"id": "exp-D", "title": "完全无关的别的话题", "zone": "候选"},
        ]
        pairs = ml.find_pairs(items, 0.75)
        self.assertEqual(len(pairs), 1)  # 只有 A/B 近似（1.0 精确重复）
        self.assertEqual(pairs[0]["sim"], 1.0)
        self.assertEqual(pairs[0]["keep"]["id"], "exp-A")

    def test_threshold_respected(self):
        items = [
            {"id": "x", "title": "数学建模竞赛答辩时间调整安排", "zone": "a"},
            {"id": "y", "title": "休息日出门撸猫采风拍照", "zone": "b"},
        ]
        self.assertEqual(ml.find_pairs(items, 0.75), [])


class T1Signals(unittest.TestCase):
    def setUp(self):
        self._orig = ns.DB
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "f.db"
        make_db(self.db)
        ns.DB = self.db

    def tearDown(self):
        ns.DB = self._orig
        self._td.cleanup()

    def test_counts(self):
        sig, _ = ns.scan_signals(0.0)
        from collections import Counter
        c = Counter(s["type"] for s in sig)
        self.assertEqual(c["tool_error"], 2)
        self.assertEqual(c["user_correction"], 1)

    def test_rhetorical_suppression(self):
        # 「对不对」不应误报（场景 6 FAIL 修复）
        self.assertFalse(ns._is_correction_hit("对不对那是学习部的事情"))
        self.assertTrue(ns._is_correction_hit("停"))

    def test_exit_code_bool_not_error(self):
        # B3-② 回归：bool 是 int 子类，exit_code:true/false 不得判为错误
        self.assertFalse(ns.is_tool_error({"exit_code": True}))
        self.assertFalse(ns.is_tool_error({"exit_code": False}))
        self.assertTrue(ns.is_tool_error({"exit_code": 1}))

    def test_array_content_recursive(self):
        # B3-③ 回归：content 为 JSON 数组时递归取首项含结构化错误字段者
        d = ns.parse_tool_content('[{"output":"x"},{"exit_code":1,"output":"err"}]')
        self.assertEqual(d.get("exit_code"), 1)
        self.assertTrue(ns.is_tool_error(d))
        # 数组内无错误字段 → _text 兜底
        d2 = ns.parse_tool_content('[{"output":"ok"}]')
        self.assertIn("_text", d2)

    def test_failed_no_colon(self):
        # B3-④ 回归：pytest 标题式 "FAILED tests/test_x.py::test"（无冒号）应判错误
        self.assertTrue(ns.is_tool_error({"output": "FAILED tests/test_x.py::test_a"}))
        self.assertTrue(ns.is_tool_error({"output": "FAILED: tests/test_x.py (col 5)"}))


class T2Extract(unittest.TestCase):
    class FakeResp:
        def __init__(self, data):
            self.data = data if isinstance(data, bytes) else data.encode()
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def test_choices_empty(self):
        # v1.0 语义（dsh 评审）：畸形 envelope 是异常响应不是「无经验」→ raise（调用方停批头）
        with mock.patch.object(en, "read_key", return_value="k"), \
             mock.patch("urllib.request.urlopen", return_value=self.FakeResp('{"choices":[]}')):
            with self.assertRaises(RuntimeError):
                en.llm_extract(["x"])

    def test_body_not_json(self):
        with mock.patch.object(en, "read_key", return_value="k"), \
             mock.patch("urllib.request.urlopen", return_value=self.FakeResp("<html>502</html>")):
            with self.assertRaises(RuntimeError):
                en.llm_extract(["x"])

    def test_content_not_str(self):
        with mock.patch.object(en, "read_key", return_value="k"), \
             mock.patch("urllib.request.urlopen", return_value=self.FakeResp(
                 '{"choices":[{"message":{"content":{"a":1}}}]}')):
            with self.assertRaises(RuntimeError):
                en.llm_extract(["x"])

    def test_dedup_before_truncate(self):
        items = [{"type": "e", "trigger": "A"}, {"type": "e", "trigger": "A"},
                 {"type": "e", "trigger": "B"}]
        with mock.patch.object(en, "read_key", return_value="k"), \
             mock.patch("urllib.request.urlopen", return_value=self.FakeResp(
                 json.dumps({"choices": [{"message": {"content": json.dumps(items, ensure_ascii=False), "finish_reason": "stop"}}]}))):
            out = en.llm_extract(["x"])
            self.assertEqual([i["trigger"] for i in out], ["A", "B"])

    def test_balanced_bracket_extract(self):
        # B3-① 回归：围栏 JSON / 混合文本含多数组 → 平衡扫描取第一个完整数组
        content = "```json\n[{\"trigger\":\"A\",\"type\":\"e\"}]\n```"
        with mock.patch.object(en, "read_key", return_value="k"), \
             mock.patch("urllib.request.urlopen", return_value=self.FakeResp(
                 json.dumps({"choices": [{"message": {"content": content, "finish_reason": "stop"}}]}))):
            out = en.llm_extract(["x"])
            self.assertEqual([i["trigger"] for i in out], ["A"])
        content2 = '前文 [{"trigger":"B","type":"e"}] 后文 [{"trigger":"C","type":"e"}]'
        with mock.patch.object(en, "read_key", return_value="k"), \
             mock.patch("urllib.request.urlopen", return_value=self.FakeResp(
                 json.dumps({"choices": [{"message": {"content": content2, "finish_reason": "stop"}}]}))):
            out = en.llm_extract(["x"])
            self.assertEqual([i["trigger"] for i in out], ["B"])  # 第一个完整数组，不被贪心吞并


class T3Adopt(unittest.TestCase):
    def test_parse_and_build(self):
        with tempfile.TemporaryDirectory() as td:
            exp = Path(td) / "experiences"
            (exp / "pending").mkdir(parents=True)
            pend = exp / "pending" / "2026-09-02.md"
            pend.write_text(
                "# 经验候选 2026-09-02\n\n"
                "- [error] **测试触发**\n"
                "    - symptom: 测试报错\n"
                "    - cause: 测试根因\n"
                "    - action: 测试做法\n"
                "    - evidence: 测试证据 | boundary: 测试边界\n"
                "    - 来源信号: tool_error / session abc\n",
                encoding="utf-8")
            self._orig = (ea.EXP_DIR, ea.PENDING_DIR, ea._date)
            ea.EXP_DIR = exp
            ea.PENDING_DIR = exp / "pending"
            try:
                real_today = ea._date.today
                ea._date = type("D", (), {"today": staticmethod(lambda _self=None: type("T", (), {"isoformat": lambda _s=None: "2026-09-02"})())})()
                rc = ea.main()
                self.assertEqual(rc, 0)
                files = list(exp.glob("exp-*.md"))
                self.assertEqual(len(files), 1)
                txt = files[0].read_text(encoding="utf-8")
                self.assertIn("id:", txt)
                self.assertIn("status: draft", txt)
                self.assertIn("provenance: agent", txt)
                self.assertIn("title: 测试触发", txt)
            finally:
                ea.EXP_DIR, ea.PENDING_DIR, ea._date = self._orig

    def test_dry_run_no_write(self):
        with tempfile.TemporaryDirectory() as td:
            exp = Path(td) / "experiences"
            (exp / "pending").mkdir(parents=True)
            pend = exp / "pending" / "2026-09-02.md"
            pend.write_text("- [error] **只预览**\n    - 来源信号: tool_error / s1\n", encoding="utf-8")
            self._orig = (ea.EXP_DIR, ea.PENDING_DIR)
            ea.EXP_DIR = exp
            ea.PENDING_DIR = exp / "pending"
            try:
                with mock.patch.object(sys, "argv", ["eco_note_adopt.py", "--dry-run"]):
                    ea.main()
                self.assertEqual(list(exp.glob("exp-*.md")), [])
            finally:
                ea.EXP_DIR, ea.PENDING_DIR = self._orig


class T4Index(unittest.TestCase):
    def test_build_empty(self):
        with tempfile.TemporaryDirectory() as td:
            txt = ei.build_index(Path(td))
            self.assertIn("暂无条目", txt)

    def test_build_with_entry(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "exp-20260902-0001.md"
            p.write_text(
                "---\nid: exp-20260902-0001\ntype: error\nstatus: verified\nlast_hit: 2026-09-02\n"
                "evidence: e1\n---\n\ntitle: 某触发\n", encoding="utf-8")
            txt = ei.build_index(Path(td))
            self.assertIn("exp-20260902-0001", txt)
            self.assertIn("verified", txt)
            self.assertIn("某触发", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
