"""eco_version._mem_pct 字符数口径回归（T0 问题集 Q3，2026-09-05）。

Bug 历史：`n // 3` 把 len() 字符数再除以 3 冒充"字数"——2941 字符（已超 2550 线）
显示为 980 字（38%），水位超限监控永不触发。
断言：健康行显示值 == 真实字符数（CJK 内容同样按 1 字符计）。
"""
import tempfile
import unittest
from pathlib import Path

import eco_version as ev


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
        # 300 个 CJK 字符：修复前显示 100（//3），修复后必须显示 300
        out = self._pct_with("记" * 300)
        self.assertTrue(out.startswith("300字/"), f"应显示真实字符数 300，实际: {out}")

    def test_over_limit_visible(self):
        # 2900 字符 > 2550 线：超限必须可见（修复前显示 966，假性健康）
        out = self._pct_with("记" * 2900)
        self.assertIn("2900字", f"超限水位必须如实显示，实际: {out}")

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
