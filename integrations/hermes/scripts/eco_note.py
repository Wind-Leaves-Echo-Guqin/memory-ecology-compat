#!/usr/bin/env python3
"""经验笔记本 · A6 捕获管线只读版（demo）

流程（设计稿 v0.2 §2.2）：
  信号命中（eco_note_signals.scan_signals 锚点预过滤）
    → 围绕信号抽取会话上下文（demo 版：信号消息前后有限窗口）
    → LLM 提取「经验候选」（五型，结构字段）
    → 写入 experiences/pending/<date>.md（候选区隔离，不直接进层）

demo 版约束（符合设计稿红线）：
  - 只写 pending/ 候选区；不写 experiences/ 正式层、不动任何正式状态
  - 不自动接纳（接纳分级/状态机/污染预检 = 正式版）
  - 水位线 = 本批 max（吸取 eco_extract LIMIT30 教训）；LLM 失败批不推进

用法:
  python eco_note.py --dry-run          # 扫信号+打印候选，不调 LLM 不写文件
  python eco_note.py                    # 真实提取并写候选区
  python eco_note.py --test             # fixture 测试（mock LLM）
"""
from __future__ import annotations

import datetime
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from eco_note_signals import scan_signals, parse_tool_content, parse_tool_calls, ECHO_MARK

from lib.config import hermes_root

HERMES = hermes_root()
DB = HERMES / "state.db"
EXP_DIR = HERMES / "experiences"
PENDING_DIR = EXP_DIR / "pending"
WATERMARK = EXP_DIR / ".watermark"
USAGE_LOG = EXP_DIR / ".usage.jsonl"   # v1.1-7a：LLM usage 日账（每成功请求一行 JSON）

MAX_SIGNALS = 8           # 单次最多处理信号簇（token 熔断，demo 保守）
CTX_BEFORE = 6            # 信号前取多少条消息
CTX_AFTER = 3             # 信号后取多少条消息
CTX_MSG_CAP = 400         # 单条消息截断长度
MAX_OUTPUT = 6            # 单次最多产出的候选条数

PROMPT = """你是经验提取器。以下是 AI agent 最近一次会话中的片段（围绕报错/用户纠正/验证动作）。
从中提取「以后还会遇到的 agent 做事经验」——已验证链路、已知坑、被证伪的尝试、通用做法。

定位说明：你输出的只是【候选】，不是最终入库。后续有独立验证/过滤机制（验证证据登记、用户确认、二次命中），
所以：宁可提取可疑候选供后续过滤，也不要遗漏片段中明确体现的经验（漏检的代价大于误报）。

规则：
1) 只依据片段中可见的事实提取；不脑补片段外的因果；
2) 分五型：error（失误/踩坑）/ success（做对了）/ link（已验证链路）/ negative（试过但失败或已失效）/ pattern（通用方法）；
3) 每条给字段：trigger（触发情境，≤60字标题）、symptom（表现/报错，若适用）、cause（根因，若适用）、action（修复或做法）、evidence（证据，若适用）、boundary（边界/反例，若适用）；
4) 片段即使不完整，只要体现出「怎么做/什么坑/什么有效」就提取；完全没有经验内容的片段才输出空数组。
输出 JSON 数组，每项格式：{"type": "...", "trigger": "...", "symptom": "...", "cause": "...", "action": "...", "evidence": "...", "boundary": "..."}。
只输出 JSON，不要其他文字。
会话片段：
"""


def read_key() -> str:
    env = HERMES / ".env"
    if not env.exists():
        raise RuntimeError(f"配置错误：{env} 不存在（无法读取 DEEPSEEK_API_KEY）")
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if not val:
                raise RuntimeError("配置错误：DEEPSEEK_API_KEY 为空（残留空值键，请检查 .env）")
            return val
    raise RuntimeError("配置错误：.env 中无 DEEPSEEK_API_KEY 行")


def fetch_context(session_id: str, anchor_id: int, before: int, after: int) -> list[str]:
    """取信号消息所在会话的上下文（只读）。返回消息文本列表（assistant 转成工具调用摘要）。"""
    import sqlite3
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, role, content, tool_name, tool_calls FROM messages "
            "WHERE session_id = ? AND id BETWEEN ? AND ? ORDER BY id ASC",
            (session_id, anchor_id - before, anchor_id + after),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    out = []
    for rid, role, content, tool_name, tool_calls in rows:
        if role == "tool":
            d = parse_tool_content(content)
            snippet = json.dumps(d, ensure_ascii=False)
            out.append(f"[tool结果 {tool_name}] {snippet[:CTX_MSG_CAP]}")
        elif role == "assistant":
            tcs = parse_tool_calls(tool_calls)
            names = ",".join(tc.get("name", "?") for tc in tcs if isinstance(tc, dict))
            out.append(f"[agent 调用] {names if names else '(文本)'} {str(content or '')[:CTX_MSG_CAP]}")
        else:
            out.append(f"[用户] {str(content or '')[:CTX_MSG_CAP]}")
    return out


def log_usage(resp: dict) -> None:
    """v1.1-7a：LLM 响应 usage 落日账（JSONL 追加）。无 usage 字段则跳过（容错旧响应）。"""
    usage = resp.get("usage")
    if not isinstance(usage, dict) or not usage.get("total_tokens"):
        return
    rec = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }
    try:
        with open(USAGE_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"⚠️ usage 记账失败（不影响提取）: {e}")


def llm_extract(fragments: list[str], max_tokens: int = 2000, retries: int = 2) -> list[dict]:
    key = read_key()
    body = json.dumps({
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": "\n".join(f"- {f}" for f in fragments)},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = r.read()
    try:
        d = json.loads(raw)
    except Exception:
        # HTTP 200 但 body 非 JSON（抗压测试场景 3：<html>502</html>）→ 视为空返回
        # 注意：返回 [] 会被 main 当成功空提取、水位线照常推进 → 信号无痕丢弃（dsh 评审）。
        # 用 sentinel 信号让调用方停批头（不推进），v1.0 语义：异常响应≠无经验。
        print(f"⚠️ llm_extract: HTTP 200 但 body 非 JSON（{raw[:60]!r}），抛 sentinel 停批头")
        raise RuntimeError("malformed-response: body 非 JSON（疑似网关/代理异常响应）")
    if not d.get("choices"):
        print(f"⚠️ llm_extract: HTTP 200 但 choices 为空（{str(d)[:60]}），抛 sentinel 停批头")
        raise RuntimeError("malformed-response: choices 为空")
    log_usage(d)  # v1.1-7a：成功请求即记账（每轮含重试；无 usage 字段自动跳过）
    msg = d["choices"][0].get("message") or {}
    content = msg.get("content") or ""
    if not isinstance(content, str):
        # 恶意 envelope：content 为 dict/list（抗压测试场景 2）→ 返回 []（main 兜底）
        print(f"⚠️ llm_extract: content 非字符串（{type(content)}），抛 sentinel 停批头")
        raise RuntimeError("malformed-response: content 非字符串")
    # finish_reason=length：reasoning 吃掉 max_tokens 后被截断（2026-09-02 实测：
    # v4-flash 是 reasoning 模型，1200 常被 reasoning 挤满、content 为空/截断）
    if d["choices"][0].get("finish_reason") == "length" and retries > 0:
        print(f"  ↻ finish_reason=length（reasoning 截断），重试 max_tokens={max_tokens * 2}")
        return llm_extract(fragments, max_tokens * 2, retries - 1)
    def _extract_items(content: str) -> list | None:
        """B3-①（2026-09-03 核验修复）：替换贪婪 `[.*]` 匹配为三级提取
        （整体 JSON → 剥 ```json 围栏 → 平衡括号扫描），防跨数组吞并/截断。"""
        def _try(cand: str) -> list | None:
            try:
                d = json.loads(cand)
                return d if isinstance(d, list) else None
            except Exception:
                return None
        r = _try(content)
        if r is not None:
            return r
        m2 = re.search(r"```(?:json)?\s*(.*?)```", content, re.S)
        r = _try(m2.group(1)) if m2 else None
        if r is not None:
            return r
        cand = content
        for start, ch in enumerate(cand):
            if ch != "[":
                continue
            depth = 0
            for pos in range(start, len(cand)):
                if cand[pos] == "[":
                    depth += 1
                elif cand[pos] == "]":
                    depth -= 1
                    if depth == 0:
                        r = _try(cand[start:pos + 1])
                        if r is not None:
                            return r
                        break
        return None

    items = _extract_items(content)
    if not items:
        return []
    # 先去重再截断（抗压测试场景 2：重复项集中在头部时唯一候选会被截丢）
    seen: set[str] = set()
    out: list[dict] = []
    for i in items:
        if not isinstance(i, dict):
            continue
        t = i.get("trigger", "") or ""
        if not t or not isinstance(t, str):
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(i)
        if len(out) >= MAX_OUTPUT:
            break
    return out


def strip_echo_lines(ctx: list[str]) -> list[str]:
    """剔除含经验注入标记的上下文行（L1 防回声：注入文本不成为提取源）。"""
    return [c for c in ctx if ECHO_MARK not in c]


def collect_fragments(signals: list[dict]) -> list[dict]:
    """对每个信号簇取上下文，去重（同会话同窗口多信号合并）。"""
    seen: set = set()
    frags: list[dict] = []
    for s in signals[:MAX_SIGNALS]:
        key = (s["session_id"], s["msg_id"])
        if key in seen:
            continue
        seen.add(key)
        ctx = strip_echo_lines(fetch_context(s["session_id"], s["msg_id"], CTX_BEFORE, CTX_AFTER))
        if ctx:
            frags.append({"signal": s, "ctx": ctx})
        else:
            # B3-⑤（2026-09-03 核验修复）：空 ctx 不再静默跳过，留诊断痕迹
            print(f"⚠️ 信号 {s['msg_id']}（{s['type']}）上下文为空，跳过提取")
    return frags


def main() -> int:
    EXP_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    wm = WATERMARK.read_text().strip() if WATERMARK.exists() else ""
    try:
        watermark = float(wm) if wm else (datetime.datetime.now().timestamp() - 86400)
    except ValueError:
        # 水位线文件损坏（2026-09-02 抗压测试发现：'abc' 裸崩溃）→ 回退默认窗口，
        # 并把损坏文件保留为 .watermark.bad-<ts> 备查，不静默覆盖
        print(f"⚠️ 水位线文件损坏（{wm[:40]!r}），回退默认窗口；损坏文件已备份")
        WATERMARK.replace(WATERMARK.with_name(f".watermark.bad-{int(datetime.datetime.now().timestamp())}"))
        watermark = datetime.datetime.now().timestamp() - 86400

    dry = "--dry-run" in sys.argv
    date = datetime.date.today().isoformat()
    out = PENDING_DIR / f"{date}.md"
    out_exists = out.exists()

    # 分批循环（吸取 eco_extract LIMIT30 教训：一次性扫全窗+截断会丢信号）：
    # 每批取窗口内最多 MAX_SIGNALS 个簇 → 处理 → 水位线推进到本批末尾。
    # 本批 LLM 有任何异常 → 批次整体不推进（下轮从本批起点重扫）。
    # 整窗无信号 / 成功空提取 → 推进（空=真没经验，迭代前进）。
    # 跨轮去重种子（抗压测试场景 2/3：单批饱和/网络失败重扫会把同批候选重复追加）：
    # 读入当天 pending 文件已有 trigger 作为 seen 初值——同一天 cron 重跑不重复写
    seen_global: set[str] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^-\s*\[\w+\]\s*\*\*(.+?)\*\*", line)
            if m:
                seen_global.add(m.group(1))

    batch_no = 0
    total_written = 0  # 跨批累计（2026-09-02 抗压测试发现：每批独立计数+break 会静默丢候选）
    batch_failed = False
    while True:
        batch_no += 1
        signals, _full_max_ts = scan_signals(watermark)
        if not signals:
            # 全窗扫描完毕
            print(f"ℹ️ 窗口处理完毕（共 {batch_no - 1} 批；水位线 {datetime.datetime.fromtimestamp(watermark).isoformat()}）")
            break
        if total_written >= MAX_OUTPUT:
            # 总候选已达单次上限：停止处理剩余信号（不浪费 LLM 调用），
            # 剩余信号留在水位线之后，下次 cron 续扫（不丢）
            print(f"ℹ️ 单次候选已达上限 {MAX_OUTPUT}，剩余 {len(signals)} 个信号留给下次续扫（水位线 {datetime.datetime.fromtimestamp(watermark).isoformat()}）")
            break
        # 信号也分批：本批只取最早 MAX_SIGNALS 个（按时间序），水位线推进到本批末尾，
        # 剩余信号下一批继续处理（不丢信号，保持 token 熔断）。
        batch_signals = signals[:MAX_SIGNALS]
        batch_max_ts = max(s["ts"] for s in batch_signals)
        if batch_max_ts <= watermark:
            # 防御：水位线不前进 = 永活循环（2026-09-02 实测）。生产上 scan_signals
            # 用 timestamp > watermark 过滤，信号 ts 恒 > watermark；此分支只在
            # 数据污染（时间戳 1970/回拨）时触发——报警退出，人工介入，绝不全速空转。
            raise RuntimeError(
                f"信号时间戳异常: batch_max_ts={batch_max_ts} <= watermark={watermark}；"
                "疑似时间戳污染（1970/回拨），需人工检查 state.db 消息时间戳"
            )
        print(f"📡 第 {batch_no} 批：信号 {len(batch_signals)}/{len(signals)} 个（窗口 → {datetime.datetime.fromtimestamp(batch_max_ts).isoformat()}）…")
        frags = collect_fragments(batch_signals)
        if not frags:
            watermark = batch_max_ts
            continue

        if dry:
            for f in frags:
                print(f"  -- [簇 {f['signal']['type']}] {f['signal']['snippet'][:80]}")
                for line in f["ctx"][:6]:
                    print(f"       {line[:100]}")
            print("ℹ️ dry-run：未调 LLM、未写文件。水位线不推进。")
            return 0

        batch_failed = False
        total_in_batch = 0
        header = f"# 经验候选 {date}（eco_note demo）\n\n" if not out_exists and batch_no == 1 else ""
        seen_cands: set[str] = set(seen_global)  # 继承跨轮种子（防重追加）
        for f in frags:
            if total_written >= MAX_OUTPUT:
                # 本批内也不继续调 LLM（防浪费）；剩余信号水位线已推，下次续扫
                print(f"  ↔ 本批候选已达上限 {MAX_OUTPUT}，停止提取本批剩余 {len(frags) - frags.index(f)} 簇（下次续扫）")
                break
            try:
                items = llm_extract(f["ctx"])
            except Exception as e:
                print(f"⚠️ 簇 {f['signal']['msg_id']} LLM 提取失败: {e}（本批不推进水位线，下轮重扫）")
                batch_failed = True
                continue
            for it in items:
                t = it.get("trigger", "") or ""
                # 恶意输入防御（2026-09-02 全量扫描实测）：LLM 可能输出 "trigger": null
                # （键存在但值为 null），.get 默认值不生效 → t=None → t[:60] 崩溃
                if not t or not isinstance(t, str):
                    continue
                # markdown 注入消毒（抗压测试场景 2，P2）：trigger 含换行/星号可伪造
                # 条目行（『…\n- [success] **伪造条目**…』）→ 换行替换为空格
                t = t.replace("\n", " ").replace("\r", " ").replace("**", "∗∗")
                if t in seen_cands:
                    continue
                seen_cands.add(t)
                total_in_batch += 1
                total_written += 1
                if total_written > MAX_OUTPUT:
                    # 超上限即停写（防超出），已累积的照常落盘
                    break
                line = (f"- [{it.get('type','?')}] **{t[:60]}**\n"
                        f"    - symptom: {it.get('symptom','')}\n"
                        f"    - cause: {it.get('cause','')}\n"
                        f"    - action: {it.get('action','')}\n"
                        f"    - evidence: {it.get('evidence','')}\n"
                        f"    - boundary: {it.get('boundary','')}\n"
                        f"    - 来源信号: {f['signal']['type']} / session {f['signal']['session_id']}\n")
                with open(out, "a", encoding="utf-8") as fh:
                    if header:
                        fh.write(header)
                        header = ""
                    fh.write(line)
                out_exists = True
        if batch_failed or total_written >= MAX_OUTPUT:
            # batch_failed：LLM 异常 → 水位线停在批头，下轮重扫
            # total_written 达上限：本批可能还有簇未处理 → 同样停在批头，
            # 下轮从批头重扫（重复提取已处理簇，但绝不丢信号；单次上限是保护性截断）
            print(f"⚠️ 本批结束状态: batch_failed={batch_failed} | 累计候选={total_written}；水位线停在 {datetime.datetime.fromtimestamp(watermark).isoformat()}，下轮续扫")
            break
        print(f"  ✅ 第 {batch_no} 批：写入 {total_in_batch} 条候选；水位线 → {datetime.datetime.fromtimestamp(batch_max_ts).isoformat()}")
        watermark = batch_max_ts

    if dry:
        print("ℹ️ dry-run：未调 LLM、未写文件。水位线不推进。")
        return 0
    if batch_failed:
        # 抗压测试（场景 3，P1）：此批 LLM/配置失败 → 非 0 退出码，
        # 否则 cron/监控把空跑误判为成功（静默停摆）
        print(f"❌ 本批 LLM 失败（batch_failed=True），返回非 0 退出码（水位线已停）")
        return 1
    WATERMARK.write_text(str(watermark))
    print(f"水位线落盘: {datetime.datetime.fromtimestamp(watermark).isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
