"""eco_version._mem_pct 字符数口径回归（T0 问题集 Q3，2026-09-05；2026-09-26 随口径单源更新）。

Bug 历史：`n // 3` 把 len() 字符数再除以 3 冒充"字数"——2941 字符（已超 2550 线）
显示为 980 字（38%），水位超限监控永不触发。
断言：健康行显示值 == 门②口径字符数（lib.metrics.chars_of——PORT_SPEC §4-C 单源，
原 len(raw) 裸字符数口径已退役：300 内容字符按门②计为 301，含条目末尾换行）。
"""
import tempfile
import unittest
from pathlib import Path

import eco_version as ev
from lib.metrics import chars_of, parse_l1


class TestMemPct(unittest.TestCase):
    def _pct_with(self, content: str) -> str:
        with tempfile.TemporaryDirectory() as td:
            mem = Path(td) / "memories"
            mem.mkdir()
            (mem / "MEMORY.md").write_text(content, encoding="utf-8")
            orig = ev.HERMES
            ev.HERMES = Path(td)
            try:
                return ev._mem_pct()
            finally:
                ev.HERMES = orig

    def test_shows_true_char_count(self):
        # 300 个 CJK 字符：修复前显示 100（//3）；口径单源后必须等于门②计法（301 = 300+末尾换行）
        out = self._pct_with("记" * 300)
        expected = chars_of(parse_l1("记" * 300)[0])
        self.assertTrue(out.startswith(f"{expected}字/"),
                        f"应显示门②口径字符数 {expected}，实际: {out}")

    def test_over_limit_visible(self):
        # 2900 字符 > 2550 线：超限必须可见（修复前显示 966，假性健康）
        out = self._pct_with("记" * 2900)
        expected = chars_of(parse_l1("记" * 2900)[0])
        self.assertIn(f"{expected}字", f"超限水位必须如实显示，实际: {out}")

    def test_missing_file_returns_question_mark(self):
        with tempfile.TemporaryDirectory() as td:
            orig = ev.HERMES
            ev.HERMES = Path(td)  # 无 memories/MEMORY.md
            try:
                self.assertEqual(ev._mem_pct(), "?")
            finally:
                ev.HERMES = orig


if __name__ == "__main__":
    unittest.main()
