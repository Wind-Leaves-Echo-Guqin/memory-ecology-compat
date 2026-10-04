#!/usr/bin/env python3
"""P0 修复回归（第 1 批，2026-10-04）

固化六项 P0 修复的行为契约：
1. 门①吞候选（P0-1）：MAX_OUTPUT 截断 / 处理失败的候选所在文件不被 mark_consumed
   静默消费——单轮 9+ 候选端到端零丢失
2. 锁纪律（P0-2/3）：死锁残留自动清理；main 异常路径锁必释放
3. 门③ § 结构（P0-4）：_replace_user_entry 行锚定重写，parse_l1 往返守恒
   （防「§ 不独占行 → 全文件解析成一条 → 超配额整体挤出 → USER.md 清空」级联）
4. eco_state CRLF（P0-5）
5. verify_entry last_verified 不双写（P0-6）
6. rewrite_status (content, changed) + `status :` 冒号前空格口径（P0-7）

隔离：patch lib.config._ENV_OVERRIDE → 临时生态根；零写生产。
运行: python test_p0_fixes.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import lib.config as libcfg
import lib.llm as lllm
import eco_review as er
import eco_state as es
import eco_note_verify as env_
import distill_stage as ds
import write_gate as wg
from lib import gatekit
from lib.memstore import mark_consumed, parse_l1

# 门① 端到端用例运行真实 add_entry（无 .env → LLM 决策 FileNotFoundError → 规则兜底，
# 全程零网络）；把生态根指到临时目录，避免读到生产 .env
_TF = tempfile.TemporaryDirectory()
libcfg._ENV_OVERRIDE = _TF.name


class TestMarkConsumedSelective(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.pending = Path(self._td.name) / "pending"
        self.pending.mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()

    def test_only_consumes_selected_files(self):
        (self.pending / "a.md").write_text("- [fact] 甲", encoding="utf-8")
        (self.pending / "b.md").write_text("- [fact] 乙", encoding="utf-8")
        (self.pending / "README.md").write_text("说明", encoding="utf-8")
        n = mark_consumed(self.pending, only={"a.md"})
        self.assertEqual(n, 1)
        self.assertTrue((self.pending / "a.md.done.md").exists())
        self.assertTrue((self.pending / "b.md").exists())  # 未终态：留下轮
        self.assertTrue((self.pending / "README.md").exists())

    def test_none_consumes_all(self):
        (self.pending / "a.md").write_text("- [fact] 甲", encoding="utf-8")
        (self.pending / "b.md").write_text("- [fact] 乙", encoding="utf-8")
        n = mark_consumed(self.pending)
        self.assertEqual(n, 2)

    def test_consumable_files_semantics(self):
        """全部候选终态才可消费；无候选文件照旧消费。"""
        (self.pending / "f1.md").write_text("占位", encoding="utf-8")
        (self.pending / "f2.md").write_text("占位", encoding="utf-8")
        (self.pending / "c.md").write_text("无 checkbox 的散文，无候选", encoding="utf-8")
        cand1 = {"file": "f1.md", "raw": "- [x] 1"}
        cand2 = {"file": "f1.md", "raw": "- [x] 2"}
        cand3 = {"file": "f2.md", "raw": "- [x] 3"}
        by_file = {"f1.md": [cand1, cand2], "f2.md": [cand3]}
        terminal = {f"{cand1['file']}|{cand1['raw']}", f"{cand3['file']}|{cand3['raw']}"}
        consume = wg.consumable_files(by_file, terminal, self.pending)
        self.assertNotIn("f1.md", consume)   # cand2 截断未处理 → f1 留下轮
        self.assertIn("f2.md", consume)
        self.assertIn("c.md", consume)       # 无候选 → 照旧消费

    def test_end_to_end_9_candidates_zero_loss(self):
        """P0-1 黄金用例：单轮 9 条候选（>MAX_OUTPUT=8）→ 处理 8 条、文件留下轮；
        第二轮处理剩余 1 条后文件才被消费。9 条全部落地，零丢失。"""
        pending = self.pending
        # 九条互异文本（两两 difflib ratio < 0.8，确保规则兜底全部走 ADD 不互吞）
        texts = [
            "用户偏好深色主题的代码编辑器",
            "部署脚本需要先运行数据库迁移",
            "上周三的团队例会讨论了缓存策略",
            "测试覆盖率报告每周五自动生成",
            "用户对香菜过敏，点餐时需注意",
            "构建失败通常是依赖版本冲突导致",
            "用户习惯用键盘快捷键而非鼠标操作",
            "数据备份任务在每天凌晨两点运行",
            "文档站点的搜索索引每周日凌晨重建",
        ]
        lines = [f"- [semantic] {t}" for t in texts]
        (pending / "batch.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        detail = Path(self._td.name) / "detail"
        logdir = Path(self._td.name) / "gate_log"
        db = Path(self._td.name) / "eco.db"

        with mock.patch.object(lllm, "complete", side_effect=AssertionError("测试禁网")):
            rc1 = wg.main(["--pending", str(pending), "--detail", str(detail),
                           "--db", str(db), "--logdir", str(logdir)])
        self.assertEqual(rc1, 0)
        done1 = list(detail.glob("*.md"))
        self.assertEqual(len(done1), wg.MAX_OUTPUT)          # 只处理 8 条
        self.assertTrue((pending / "batch.md").exists())     # 未终态 → 不消费

        rc2 = wg.main(["--pending", str(pending), "--detail", str(detail),
                       "--db", str(db), "--logdir", str(logdir)])
        self.assertEqual(rc2, 0)
        done2 = list(detail.glob("*.md"))
        self.assertEqual(len(done2), 9)                      # 第 9 条补上
        self.assertTrue((pending / "batch.md.done.md").exists())  # 全部终态才消费
        self.assertFalse((pending / "batch.md").exists())

    def test_failed_candidate_not_consumed(self):
        """处理失败的候选：文件不消费（留待重试），且失败计入返回码。"""
        pending = self.pending
        (pending / "x.md").write_text("- [semantic] 正常候选甲\n- [semantic] 正常候选乙",
                                      encoding="utf-8")
        detail = Path(self._td.name) / "detail"
        db = Path(self._td.name) / "eco.db"
        with mock.patch.object(lllm, "complete", side_effect=AssertionError("测试禁网")), \
             mock.patch.object(wg, "add_entry", side_effect=OSError("注入写失败")):
            rc = wg.main(["--pending", str(pending), "--detail", str(detail),
                          "--db", str(db), "--logdir", str(Path(self._td.name) / "log")])
        self.assertEqual(rc, 1)                              # 有失败
        self.assertFalse((pending / "x.md.done.md").exists())  # 失败 → 不消费
        self.assertTrue((pending / "x.md").exists())


class TestLockDiscipline(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.lock = Path(self._td.name) / ".test_gate.lock"

    def tearDown(self):
        self._td.cleanup()

    def _dead_pid(self) -> int:
        """造一个确定已死的 pid：起子进程等它退出（跨平台可靠）。"""
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        deadline = time.time() + 2
        while gatekit._pid_alive(p.pid) and time.time() < deadline:
            time.sleep(0.05)
        return p.pid

    def test_stale_lock_cleared(self):
        dead = self._dead_pid()
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        self.lock.write_text(str(dead), encoding="utf-8")
        ok, how = gatekit.acquire_lock_auto(self.lock)
        self.assertTrue(ok)
        self.assertEqual(how, "stale-cleared")
        self.assertEqual(gatekit.read_lock_pid(self.lock), os.getpid())
        gatekit.release_lock(self.lock)

    def test_live_lock_held(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            self.lock.write_text(str(p.pid), encoding="utf-8")
            ok, how = gatekit.acquire_lock_auto(self.lock)
            self.assertFalse(ok)
            self.assertEqual(how, "held")
        finally:
            p.kill()
            p.wait()

    def test_corrupt_lock_conservative_held(self):
        self.lock.write_text("not-a-pid", encoding="utf-8")
        ok, how = gatekit.acquire_lock_auto(self.lock)
        self.assertFalse(ok)
        self.assertEqual(how, "held")       # 保守：不误删看不懂的锁
        self.assertTrue(self.lock.exists())

    def test_write_gate_main_releases_lock_on_exception(self):
        """main 异常穿透时锁也必须释放（P0-2 try/finally）。"""
        orig_lock = wg.LOCK
        orig_run = wg._run
        wg.LOCK = self.lock
        def _boom(args):
            raise RuntimeError("注入异常")
        wg._run = _boom
        try:
            with self.assertRaises(RuntimeError):
                wg.main([])
            self.assertFalse(self.lock.exists())   # finally 释放
        finally:
            wg.LOCK = orig_lock
            wg._run = orig_run

    def test_write_gate_main_releases_lock_on_success(self):
        orig_lock = wg.LOCK
        orig_run = wg._run
        wg.LOCK = self.lock
        wg._run = lambda args: 0
        try:
            self.assertEqual(wg.main([]), 0)
            self.assertFalse(self.lock.exists())
        finally:
            wg.LOCK = orig_lock
            wg._run = orig_run


class TestReplaceUserEntry(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.uf = Path(self._td.name) / "USER.md"

    def tearDown(self):
        self._td.cleanup()

    def test_replace_preserves_section_structure(self):
        self.uf.write_text("特质甲的内容\n§\n特质乙的内容\n", encoding="utf-8")
        ok = ds._replace_user_entry(self.uf, "特质甲的内容", "新特质")
        self.assertTrue(ok)
        out = self.uf.read_text(encoding="utf-8")
        # P0-4 黄金断言：§ 仍独占一行（旧实现产出 "新特质\n§特质乙的内容\n" 级联毁文件）
        self.assertEqual(out, "新特质\n§\n特质乙的内容\n")
        self.assertEqual(parse_l1(out)[0], ["新特质", "特质乙的内容"])

    def test_replace_preserves_crlf(self):
        self.uf.write_text("甲\r\n§\r\n乙\r\n", encoding="utf-8", newline="")
        ok = ds._replace_user_entry(self.uf, "甲", "X")
        self.assertTrue(ok)
        with self.uf.open("r", encoding="utf-8", newline="") as f:  # 读也关换行转换
            self.assertEqual(f.read(), "X\r\n§\r\n乙\r\n")

    def test_replace_missing_block_returns_false(self):
        self.uf.write_text("甲\n§\n乙\n", encoding="utf-8")
        ok = ds._replace_user_entry(self.uf, "不存在的块", "X")
        self.assertFalse(ok)
        self.assertEqual(self.uf.read_text(encoding="utf-8"), "甲\n§\n乙\n")

    def test_user_entries_line_anchored(self):
        self.uf.write_text("甲\n§\n乙\n", encoding="utf-8")
        self.assertEqual(ds.user_entries(self.uf), ["甲", "乙"])


class TestEcoStateCRLF(unittest.TestCase):
    def test_lf(self):
        fm = es.parse_frontmatter("---\nstatus: candidate\nfate: retained\n---\nbody")
        self.assertEqual(fm["status"], "candidate")
        self.assertEqual(fm["fate"], "retained")

    def test_crlf(self):
        """P0-5：CRLF 文件旧版整体解析失败 → 全部 undeclared 静默漏检。"""
        fm = es.parse_frontmatter("---\r\nstatus: candidate\r\nfate: retained\r\n---\r\nbody")
        self.assertEqual(fm["status"], "candidate")
        self.assertEqual(fm["fate"], "retained")

    def test_bom_crlf(self):
        fm = es.parse_frontmatter("\ufeff---\r\nstatus: active\r\nfate: retained\r\n---\r\nb")
        self.assertEqual(fm["status"], "active")


class TestVerifyEntryNoDuplicate(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._td.cleanup()

    def _entry(self, text: str) -> Path:
        p = Path(self._td.name) / "exp-20260101-0001.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_existing_lv_not_duplicated(self):
        p = self._entry("---\nid: e1\nstatus: draft\nlast_verified: 2026-01-01\nlast_hit: 2026-01-01\n---\n正文")
        ok, _ = env_.verify_entry(p)
        self.assertTrue(ok)
        out = p.read_text(encoding="utf-8")
        self.assertEqual(out.count("last_verified:"), 1)   # P0-6：旧实现两行
        self.assertEqual(out.count("status: verified"), 1)

    def test_missing_lv_inserted_once(self):
        p = self._entry("---\nid: e2\nstatus: draft\n---\n正文")
        ok, _ = env_.verify_entry(p)
        self.assertTrue(ok)
        self.assertEqual(p.read_text(encoding="utf-8").count("last_verified:"), 1)


class TestRewriteStatusContract(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._td.cleanup()

    def test_space_before_colon_now_rewritten(self):
        """P0-7：`status :`（冒号前空格）解析层认、重写层也必须认——旧版静默空写。"""
        raw = "---\nstatus : active\nlast_verified: 2026-01-01\n---\n正文"
        out, changed = er.rewrite_status(raw, "dormant", refresh_lv=True)
        self.assertTrue(changed)
        self.assertIn("status: dormant", out)
        self.assertNotIn("status :", out)

    def test_missing_status_returns_changed_false(self):
        raw = "---\ntype: semantic\n---\n正文"
        out, changed = er.rewrite_status(raw, "dormant")
        self.assertFalse(changed)
        self.assertEqual(out, raw)                          # 原样返回，未动

    def test_missing_lv_inserted_after_status(self):
        raw = "---\nstatus: active\ntype: semantic\n---\n正文"
        out, changed = er.rewrite_status(raw, "dormant", refresh_lv=True)
        self.assertTrue(changed)
        self.assertEqual(out.count("last_verified:"), 1)
        lines = out.splitlines()
        self.assertLess(lines.index("status: dormant"),
                        next(i for i, l in enumerate(lines) if l.startswith("last_verified")))

    def test_body_hr_not_touched(self):
        """正文里的 --- 水平线不得重新进入 frontmatter 误改（旧版 in_fm 逐个切换的隐患）。"""
        raw = "---\nstatus: active\n---\n\n---\nstatus: 这不是frontmatter\n"
        out, changed = er.rewrite_status(raw, "dormant")
        self.assertTrue(changed)
        self.assertIn("status: 这不是frontmatter", out)     # 正文原样

    def test_bom_preserved(self):
        raw = "\ufeff---\nstatus: active\n---\n正文"
        out, changed = er.rewrite_status(raw, "dormant")
        self.assertTrue(changed)
        self.assertTrue(out.startswith("\ufeff"))

    def test_mark_dormant_reports_unmatched(self):
        """调用方契约：changed=False 时计入失败（返回 1），不写文件不假成功。"""
        rec = {"slug": "x", "raw": "---\ntype: semantic\n---\n正文", "path": Path("x.md"),
               "action": ("mark_dormant", "r")}
        archive = Path(self._td.name) / "archive"
        rc = er.apply_detail_actions([rec], archive, dry_run=False)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
