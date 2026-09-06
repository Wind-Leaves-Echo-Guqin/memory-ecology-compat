#!/usr/bin/env python3
"""经验笔记本 · A5 信号检测模块（demo 版）

用途：从 Hermes state.db 扫描「经验信号」——工具错误 / 用户纠正 / 验证动作，
作为 LLM 提取前的锚点预过滤（设计稿 v0.2 §2.1/§2.2）。

demo 版通过扫描 state.db 实现（不挂 shell hook，零风险）；
正式版将升级为 hook 事件驱动（设计稿 2.1 表）。

用法（自测）:
    python eco_note_signals.py --since <unix_ts> --limit <n>   # 扫描自 X 以来的信号
    python eco_note_signals.py --test                           # 用合成 fixture 自测

规则（保守，宁缺毋滥）:
  1. tool_error: role='tool' 的 content JSON 含 error 结构
     （exit_code != 0 / status in (error,failed) / error 字段非空）
  2. user_correction: 用户消息（长度 < 60）命中纠正词表，且时间上靠近工具轮
  3. verify_action: 工具调用名/参数含 verify | test | check | 验证（evidence 来源）

预过滤只做标记不做判定；LLM 提取 + 候选区隔离兜底。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

HERMES = Path(__file__).resolve().parent.parent.parent
# 生产 state.db 位置（demo 只读；不写任何东西）
DB = HERMES / "state.db"
if not DB.exists():
    from lib.config import hermes_root

    DB = hermes_root() / "state.db"

# 纠正词表（保守集合；「停/别…」系短句，命中即候选，误报交给 LLM 兜底）
# 2026-09-02 抗压测试：裸子串命中误报（「对不对」含「不对」）——匹配时排除
# 否定词嵌在反问/疑问句式内的情形（对不对/是不是/行不行 等），见 _is_correction_hit
CORR_WORDS = (
    "停", "别搜", "别查", "别急", "错了", "不对", "不是这个", "不是那",
    "太慢", "算了", "重来", "重做", "重新", "你看", "记得", "有问题",
    "记错", "口吻不对", "不要这样", "别这样", "搞错了",
)

# 反问前缀（词命中但紧邻「对/是」= 疑问句模式，正常话语不误报）
_RHETORICAL = ("对不对", "是不是", "行不行", "好不好", "要不要", "该不该")


def _is_correction_hit(text: str) -> bool:
    """用户消息是否命中纠正词表（带反问句式抑制）。

    2026-09-02 抗压测试（场景 6 FAIL）：「对不对那是学习部的事」内含「不对」子串
    被误判为纠正。抑制规则：若否定词命中点与任一 _RHETORICAL 反问词的区间重叠，
    则这次命中属反问式（非纠正），跳过。
    """
    for w in CORR_WORDS:
        start = 0
        while True:
            i = text.find(w, start)
            if i < 0:
                break
            # 该命中点是否落在某个反问词区间内
            suppressed = False
            for rh in _RHETORICAL:
                rj = text.find(rh)
                while rj >= 0:
                    if rj <= i < rj + len(rh) or rj <= i + len(w) <= rj + len(rh):
                        suppressed = True
                        break
                    rj = text.find(rh, rj + 1)
                if suppressed:
                    break
            if not suppressed:
                return True
            start = i + 1
    return False

# 验证动作关键词（evidence 来源；设计稿 1.4 工程型验证证据）
VERIFY_WORDS = ("verify", "test", "check", "验证", "校验", "复跑", "断言", "fixture")

# 经验注入回声防护标记（2026-09-03 L1）：注入文本带此标记，
# 信号扫描/上下文提取时排除含该标记的消息（防「注入内容被二次提取为经验」）。
ECHO_MARK = "【经验参考"

# v1.1-3a（2026-09-02 双审采纳）：verify_action 降级出捕获。
# 依据：占信号 77%、产物未测、误报面大（arguments 含工具全文内容，写 "test" 即命中——
# 任何带 test 字样的文件写入都会触发一簇 LLM 提取）。降级后：
#   - 不再作为独立信号（默认 False），验证类经验改经 evidence 登记通道流入（eco_note_verify.py）
#   - 开关保留（置 True 即恢复 v1.0 行为，可回滚）
VERIFY_AS_SIGNAL = False


def parse_tool_content(content: str) -> dict:
    """解析 tool 消息 content（可能是 JSON 字符串或纯文本）。

    B3-③（2026-09-03 核验修复）：content 为 JSON 数组（约 5.8%）时递归取第一个
    含错误结构化字段的 dict 项（旧实现整包丢弃为 {}）。
    """
    if not content:
        return {}
    try:
        d = json.loads(content)
        if isinstance(d, list):
            for item in d:
                if isinstance(item, dict) and (
                    "exit_code" in item or "status" in item or "error" in item
                ):
                    return item
            return {"_text": content}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {"_text": content}


def is_tool_error(d: dict) -> bool:
    """工具结果是否含错误信号。

    只认结构化信号（exit_code/status/error 字段）+ output 文本里的明确错误
    特征（Traceback/ModuleNotFoundError/FAILED:），不做宽松文本匹配（避免 web 结果
    等普通内容被误判——2026-09-02 demo 实测误报修复）。
    2026-09-02 抗压测试补充：纯文本 content（非 JSON，_text 形态）的生产真错
    误（如 "Error executing tool 'terminal': timed out after 420.0s"，生产库
    实测 3 条）——用带引号工具名/计时特征的精确前缀匹配，避免宽泛 Error: 误报。
    """
    if not d:
        return False
    ec = d.get("exit_code")
    if isinstance(ec, bool):
        # B3-②（2026-09-03 核验修复）：bool 是 int 子类，exit_code:true 会被误判为错误
        ec = None
    if isinstance(ec, int) and ec != 0:
        return True
    st = str(d.get("status", "")).lower()
    if st in ("error", "failed", "failure", "false"):
        return True
    if str(d.get("error") or d.get("error_detail") or "") not in ("", "None"):
        return True
    out = str(d.get("output", ""))
    if re.search(r"(ModuleNotFoundError|Traceback \(most recent call last\)|FAILED:|\bFAILED\s)", out, re.I):
        # B3-④（2026-09-03 核验修复）：pytest 报告形态 "FAILED tests/test_x.py::test"（无冒号）
        # 旧判据仅匹配 "FAILED:"，漏检标题式 FAILED 行
        return True
    txt = str(d.get("_text", ""))
    if re.search(r"Error executing tool ['\"]\w+['\"]:\s*(timed out|timeout|failed)", txt, re.I):
        return True
    return False


def parse_tool_calls(tool_calls_json: str | None) -> list[dict]:
    """解析 assistant 消息的 tool_calls JSON。

    兼容两种结构：
      A) OpenAI 形: {"id":..., "type":"function", "function":{"name":..., "arguments":...}}
      B) 扁平行:   {"name":..., "arguments":...}
    返回统一 [{name, arguments}] 列表（2026-09-02：真实库为 A 形，旧解析器只认顶层
    name/arguments，导致 verify_action 信号全盲——已修）。
    """
    if not tool_calls_json:
        return []
    try:
        d = json.loads(tool_calls_json)
        items = d if isinstance(d, list) else (d.get("tool_calls", []) if isinstance(d, dict) else [])
    except Exception:
        return []
    out: list[dict] = []
    for tc in items:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if isinstance(fn, dict):
            out.append({"name": fn.get("name", ""), "arguments": fn.get("arguments", "")})
        elif tc.get("name"):
            out.append({"name": tc.get("name", ""), "arguments": tc.get("arguments", "")})
    return out


def scan_signals(watermark: float, limit: int = 5000) -> tuple[list[dict], float]:
    """扫描 state.db（只读），返回 (信号列表, 新水位线)。

    信号 dict: {type, session_id, ts, tool_name, snippet}
    水位线推进 = 扫描窗口的最大 timestamp（本批 max，吸取 eco_extract LIMIT30 教训：
    不推进全表 MAX，避免跳窗；此处整窗单批扫描，失败即整体不推进）。
    """
    if not DB.exists():
        raise FileNotFoundError(f"state.db 不存在: {DB}")
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        # 一次取窗口内全部消息（demo 期保守 limit，正式版分批）
        cur.execute(
            "SELECT id, session_id, role, content, tool_name, tool_calls, timestamp "
            "FROM messages WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?",
            (watermark, limit),
        )
        rows = cur.fetchall()
        signals: list[dict] = []
        max_ts = watermark
        for rid, sid, role, content, tool_name, tool_calls, ts in rows:
            max_ts = max(max_ts, ts)
            if role == "tool":
                d = parse_tool_content(content)
                if is_tool_error(d):
                    signals.append({
                        "type": "tool_error",
                        "session_id": sid,
                        "ts": ts,
                        "tool_name": tool_name or "?",
                        "snippet": json.dumps(d, ensure_ascii=False)[:120],
                        "msg_id": rid,
                    })
            elif role == "user":
                text = (content or "").strip()
                if 0 < len(text) < 60 and _is_correction_hit(text):
                    signals.append({
                        "type": "user_correction",
                        "session_id": sid,
                        "ts": ts,
                        "tool_name": "user",
                        "snippet": text[:60],
                        "msg_id": rid,
                    })
            elif role == "assistant":
                for tc in parse_tool_calls(tool_calls):
                    name = tc.get("name", "") if isinstance(tc, dict) else ""
                    args = tc.get("arguments", "")
                    if isinstance(args, str):
                        args_txt = args
                    else:
                        args_txt = json.dumps(args, ensure_ascii=False)
                    if VERIFY_AS_SIGNAL and any(w in (name + args_txt).lower() for w in VERIFY_WORDS):
                        signals.append({
                            "type": "verify_action",
                            "session_id": sid,
                            "ts": ts,
                            "tool_name": name or "?",
                            "snippet": args_txt[:80],
                            "msg_id": rid,
                        })
        return signals, max_ts
    finally:
        conn.close()


def brief(signals: list[dict]) -> str:
    from collections import Counter
    c = Counter(s["type"] for s in signals)
    parts = [f"{k}:{v}" for k, v in c.items()]
    return ", ".join(parts) if parts else "无"


# ---------------- 合成 fixture 自测 ----------------

def make_fixture_db(path: Path) -> Path:
    """构造合成 state.db（仅消息表子集），用于隔离测试。"""
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
    ]
    conn.executemany(
        "INSERT INTO messages (id, session_id, role, content, tool_name, tool_calls, timestamp) "
        "VALUES (?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()
    return path


def test() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        db = make_fixture_db(Path(td) / "fixture.db")
        global DB
        DB = db
        signals, _ = scan_signals(0.0)
    expect = {"tool_error": 3, "user_correction": 1, "verify_action": 0}
    from collections import Counter
    got = Counter(s["type"] for s in signals)
    ok = all(got.get(k, 0) == v for k, v in expect.items())
    print(f"信号检测 fixture: {dict(got)} vs 期望 {expect} → {'PASS' if ok else 'FAIL'}")
    # 期望明细：tool_error=msg3(exit 1)/msg6(error)/msg10(Traceback)；user_correction=msg4(停)
    # verify_action=msg7(test) —— v1.1-3a：验证动作已降级出捕获（VERIFY_AS_SIGNAL=False），期望 0
    return 0 if ok else 1


def main() -> int:
    if "--test" in sys.argv:
        return test()
    since = float(sys.argv[sys.argv.index("--since") + 1]) if "--since" in sys.argv else 0.0
    limit = int(
        sys.argv[sys.argv.index("--limit") + 1] if "--limit" in sys.argv else 5000
    )
    signals, new_wm = scan_signals(since, limit)
    print(f"信号统计: {brief(signals)} | 新水位线: {new_wm}")
    for s in signals[:30]:
        print(f"  [{s['type']}] {s['session_id'][:12]} ts={int(s['ts'])} {s['tool_name']}: {s['snippet'][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
