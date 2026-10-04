"""lib/safeio 安全写路径测试（v2.2.0 首项）。

覆盖：路径穿越拒绝 / 白名单 / schema 枚举与日期与计数校验 / 原子写 / 备份 / JSONL 容量护栏。
运行: python test_safeio.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from lib import safeio


class SafePathTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_rejects_traversal(self):
        for bad in ("..", "a/../..", "a/b", "a\\b", "", "  "):
            with self.assertRaises(ValueError, msg=bad):
                safeio.safe_entry_path(self.root, bad)

    def test_rejects_bad_chars(self):
        for bad in ("a:b", "a|b", "a*b", "a?b", "a\nb"):
            with self.assertRaises(ValueError):
                safeio.safe_entry_path(self.root, bad)

    def test_accepts_valid_and_confines(self):
        p = safeio.safe_entry_path(self.root, "exp-20260906-0001")
        self.assertEqual(p.parent, self.root)
        self.assertEqual(p.suffix, ".md")
        p2 = safeio.safe_entry_path(self.root, "记忆条目-中文")
        self.assertEqual(p2.parent, self.root)


class SchemaTest(unittest.TestCase):
    def test_detail_enum_and_dates(self):
        self.assertEqual(safeio.validate_detail_fm({"type": "semantic", "status": "active"}), [])
        errs = safeio.validate_detail_fm({"type": "fact", "status": "archived", "last_seen": "2026/09/06"})
        self.assertTrue(any("type" in e for e in errs))
        self.assertTrue(any("status" in e for e in errs))
        self.assertTrue(any("last_seen" in e for e in errs))

    def test_detail_counts(self):
        self.assertEqual(safeio.validate_detail_fm({"occurrences": "3"}), [])
        errs = safeio.validate_detail_fm({"occurrences": "x", "session_count": "-1"})
        self.assertEqual(len(errs), 2)

    def test_experience_enum(self):
        # R9：error 型必填 symptom/cause（写在正文）——空 body 视为缺字段，故此处显式给全
        self.assertEqual(safeio.validate_experience_fm(
            {"type": "error", "status": "verified"}, "symptom: s\ncause: c"), [])
        errs = safeio.validate_experience_fm({"type": "semantic", "status": "active"})
        self.assertEqual(len(errs), 2)


class WriteEntryTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_write_validates_and_raises(self):
        p = self.root / "exp-x.md"
        bad = "---\nid: exp-x\ntype: semantic\nstatus: draft\n---\n正文"
        with self.assertRaises(ValueError):
            safeio.write_entry(p, bad, kind="experience")
        self.assertFalse(p.exists())  # 校验失败不落盘

    def test_write_ok_with_backup(self):
        p = self.root / "exp-y.md"
        good = "---\nid: exp-y\ntype: error\nstatus: draft\n---\ntitle: t\nsymptom: s\ncause: c"
        p.write_text(good, encoding="utf-8")
        new = good.replace("status: draft", "status: verified")
        safeio.write_entry(p, new, kind="experience", backup_tag="test")
        self.assertIn("status: verified", p.read_text(encoding="utf-8"))
        baks = list(self.root.glob("*.bak-test-*"))
        self.assertEqual(len(baks), 1)
        self.assertIn("status: draft", baks[0].read_text(encoding="utf-8"))


class AppendJsonlTest(unittest.TestCase):
    def test_append_and_cap(self):
        p = Path(tempfile.gettempdir()) / f"_safeio_test_{id(self)}.jsonl"
        try:
            safeio.append_jsonl(p, {"a": 1})
            safeio.append_jsonl(p, {"b": "中文"})
            lines = p.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            # 容量护栏：超限静默停写
            safeio.append_jsonl(p, {"c": 3}, max_bytes=1)
            self.assertEqual(len(p.read_text(encoding="utf-8").splitlines()), 2)
        finally:
            p.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
