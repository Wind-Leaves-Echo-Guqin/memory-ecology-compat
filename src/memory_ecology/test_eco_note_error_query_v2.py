"""根因分层回归（兼容版 Q2 修复）：异常类名提取 + 同根因优先/异根因沉底。

Q2 病灶复现：NameError（逻辑粗心）查询不得命中「缺库」经验——旧版按词匹配时
两者词面命中数相同，文件序靠前的缺库条目会排第一（T0 问题集 Q2 真实事故）。
"""
import tempfile
import unittest
from pathlib import Path

import eco_note_query as eq
import eco_note_error_query as errq

ENTRY_0001 = """---
id: exp-test-0001
type: error
status: draft
created: 2026-09-05T10:00:00
provenance: agent
source: sess-a | tool_error
distilled_to: ''
---

title: 在默认 Python 环境中直接运行数据脚本
symptom: python script failed: ModuleNotFoundError: No module named 'xarray'
cause: 默认 Python 环境缺 xarray
action: 先定位实际使用的环境再运行
evidence: python -c 报缺 xarray | boundary: 环境已装则不适用
"""

ENTRY_0002 = """---
id: exp-test-0002
type: error
status: draft
created: 2026-09-05T10:01:00
provenance: agent
source: sess-b | tool_error
distilled_to: ''
---

title: bash 单引号内变量未展开导致名称未定义
symptom: NameError: name 'H' is not defined
cause: bash 单引号里 $H 不展开，python 收到字面量
action: python -c 改双引号或预先 export
evidence: NameError 复现两次 | boundary: 真实未 import 时不适用
"""

ENTRY_0003 = """---
id: exp-test-0003
type: pattern
status: draft
created: 2026-09-05T10:02:00
provenance: agent
source: sess-c | user_correction
distilled_to: ''
---

title: 运行前先确认解释器与环境
symptom: 命令行为与预期不符
cause: 解释器路径假设错误
action: 先 which 确认再运行
evidence: 多次环境假设出错 | boundary: 无
"""


class TestRootCauseTier(unittest.TestCase):
    def test_extract_exceptions(self):
        self.assertEqual(errq.extract_exceptions("NameError: x / ValueError: y"),
                         {"NameError", "ValueError"})
        self.assertEqual(errq.extract_exceptions("没有异常"), set())

    def test_tier_matrix(self):
        q = {"NameError"}
        self.assertEqual(errq._root_cause_tier(q, "撞见 NameError 了"), 0)
        self.assertEqual(errq._root_cause_tier(q, "纯泛化经验无异常名"), 1)
        self.assertEqual(errq._root_cause_tier(q, "ModuleNotFoundError 缺库"), 2)


class TestRankWithRootCause(unittest.TestCase):
    def setUp(self):
        self._orig = eq.EXP_DIR
        self.tmp = tempfile.TemporaryDirectory()
        exp = Path(self.tmp.name) / "experiences"
        exp.mkdir()
        (exp / "exp-test-0001.md").write_text(ENTRY_0001, encoding="utf-8")
        (exp / "exp-test-0002.md").write_text(ENTRY_0002, encoding="utf-8")
        (exp / "exp-test-0003.md").write_text(ENTRY_0003, encoding="utf-8")
        eq.EXP_DIR = exp

    def tearDown(self):
        eq.EXP_DIR = self._orig
        self.tmp.cleanup()

    def test_q2_regression_same_root_cause_first(self):
        # 词面命中数相同（0001: python+script=2；0002: NameError+defined=2），
        # 旧版按文件序会把缺库条目排第一；根因分层后 NameError 查询必须 0002 第一
        query = "python script failed: NameError: name 'H' is not defined"
        order = [h["path"] for h in errq.rank(query, top=3)]
        self.assertEqual(order[0], "exp-test-0002", f"同根因应第一，实际: {order}")
        self.assertEqual(order[-1], "exp-test-0001", f"异根因应沉底，实际: {order}")

    def test_generic_query_keeps_old_behavior(self):
        # 查询无异常名 → 不分层（旧行为原样保留）
        order = [h["path"] for h in errq.rank("xarray", top=3)]
        self.assertEqual(order, ["exp-test-0001"])

    def test_build_json_shape(self):
        payload = errq.build_json("NameError: name 'H' is not defined", top=3)
        self.assertIn("hits", payload)
        self.assertIn("query_exc", payload)
        self.assertEqual(payload["query_exc"], ["NameError"])
        if payload["hits"]:
            self.assertIn("id", payload["hits"][0])
            self.assertIn("status", payload["hits"][0])


if __name__ == "__main__":
    unittest.main()
