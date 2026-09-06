"""dsh 上下文适配器测试：会话报错提取 → 根因注入 → 冷却/去重状态机。

fixture 全隔离（tmp 会话根 + tmp 经验库），零 LLM、零真实数据。
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import eco_note_dsh_context as ctx  # noqa: E402
import eco_note_query as eq  # noqa: E402

def _find_scripts_test(name: str) -> Path:
    for base in HERE.parents:  # package/python -> dsh -> adapters|integrations -> repo root
        for rel in (("scripts",), ("src", "memory_ecology")):
            cand = base.joinpath(*rel) / name
            if cand.exists():
                return cand
    raise FileNotFoundError(name)


ENTRY_NAMEERROR = _find_scripts_test("test_eco_note_error_query_v2.py")


def _write_entry(exp_dir: Path, name: str, text: str) -> None:
    (exp_dir / name).write_text(text, encoding="utf-8")


E1 = open(ENTRY_NAMEERROR, encoding="utf-8").read()
# 复用根因回归测试里的 fixture 条目（0001 缺库 / 0002 NameError / 0003 泛化）
ENTRY_0001 = E1.split('ENTRY_0001 = """')[1].split('"""')[0] + "\n"
ENTRY_0002 = E1.split('ENTRY_0002 = """')[1].split('"""')[0] + "\n"
ENTRY_0003 = E1.split('ENTRY_0003 = """')[1].split('"""')[0] + "\n"

ERR_LINE = json.dumps({"role": "tool", "content":
                       "python script failed: NameError: name 'H' is not defined"},
                      ensure_ascii=False)


class TestDshContext(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sessions = root / "sessions"
        self.exp = root / "experiences"
        self.exp.mkdir()
        self.sess_dir = self.sessions / "--C-tmp--" / "session-abc123"
        self.sess_dir.mkdir(parents=True)
        # Q26/Q27 修复配套（2026-09-06）：真实会话文件名为 session.jsonl.zstd（UUID 在
        # 目录名），且为流式压缩（帧头无内容大小）——原夹具的 session-abc123 前缀 +
        # embed 大小的 compress 恰好掩盖了 glob 错误与解压必败两个 bug
        self.sess_file = self.sess_dir / "session.jsonl.zstd"
        import io
        import zstandard
        payload = "\n".join([
            json.dumps({"role": "user", "content": "帮我跑一下"}, ensure_ascii=False),
            ERR_LINE,
        ])
        buf = io.BytesIO()
        with zstandard.ZstdCompressor().stream_writer(buf, closefd=False) as w:
            w.write(payload.encode("utf-8"))
        self.sess_file.write_bytes(buf.getvalue())
        _write_entry(self.exp, "exp-test-0001.md", ENTRY_0001)
        _write_entry(self.exp, "exp-test-0002.md", ENTRY_0002)
        _write_entry(self.exp, "exp-test-0003.md", ENTRY_0003)
        self._orig_exp = eq.EXP_DIR
        eq.EXP_DIR = self.exp

    def tearDown(self):
        eq.EXP_DIR = self._orig_exp
        self.tmp.cleanup()

    def _set_mtime(self, t: float) -> None:
        os.utime(self.sess_file, (t, t))

    def test_inject_prefers_same_root_cause(self):
        now = time.time()
        self._set_mtime(now)
        out = ctx.run(self.sessions, window_min=10, cooldown_min=15, now=now)
        self.assertEqual(out["reason"], "ok")
        self.assertIn("exp-test-0002", out["inject"])
        self.assertIn(ctx.ECHO_MARK, out["inject"])   # 防回声标记必须在
        self.assertEqual(out["episodes"][0], "sess-b | tool_error")  # 同根因第一

    def test_no_recent_error(self):
        now = time.time()
        self._set_mtime(now - 3600)  # 1 小时前，窗口 10 分钟外
        out = ctx.run(self.sessions, window_min=10, now=now)
        self.assertIsNone(out["inject"])
        self.assertEqual(out["reason"], "no-recent-error")

    def test_cooldown_blocks_second_injection(self):
        now = time.time()
        self._set_mtime(now)
        out1 = ctx.run(self.sessions, window_min=10, cooldown_min=15, now=now)
        self.assertEqual(out1["reason"], "ok")
        out2 = ctx.run(self.sessions, window_min=10, cooldown_min=15, now=now + 60)
        self.assertIsNone(out2["inject"])
        self.assertEqual(out2["reason"], "cooldown")

    def test_episodes_seen_after_cooldown(self):
        now = time.time()
        self._set_mtime(now)
        ctx.run(self.sessions, window_min=10, cooldown_min=15, now=now)
        # 冷却刚过、同一 episode → 不重复推（活跃会话 mtime 持续刷新）
        now2 = now + 16 * 60
        self._set_mtime(now2)
        out = ctx.run(self.sessions, window_min=10, cooldown_min=15, now=now2)
        self.assertIsNone(out["inject"])
        self.assertEqual(out["reason"], "episodes-seen")

    def test_missing_sessions_root(self):
        out = ctx.run(Path(self.tmp.name) / "nope", window_min=10, now=time.time())
        self.assertIsNone(out["inject"])
        self.assertEqual(out["reason"], "no-recent-error")


if __name__ == "__main__":
    unittest.main()
