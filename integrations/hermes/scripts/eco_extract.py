#!/usr/bin/env python3
"""生态捕获骨架（会话记录增量提取，捕获主通道）。

LLM 提取版：扫描 state.db 新增会话的用户消息（限量采样），用 deepseek-v4-flash
提炼「值得长期记住的稳定事实/偏好/纠正」候选，写入 memories/pending/<date>.md
候选区（待确认，不直接进记忆）。

设计：LLM 主通道 + 保守 prompt（宁缺毋滥）+ 硬熔断（条数/输出上限，token 可控）。
用法: python eco_extract.py   （cron 每日 13:00 no_agent）
"""
import datetime
import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

from lib.config import hermes_root

HERMES = hermes_root()
DB = HERMES / "state.db"
PENDING_DIR = HERMES / "memories" / "pending"
WATERMARK = PENDING_DIR / ".watermark"
MAX_MSGS = 30          # 单次扫描最多取的用户消息条数（token 熔断）
MAX_OUTPUT = 5         # 单次最多产出的候选条数
PROMPT = """你是记忆提取器。以下是用户今天在 AI 助手会话中发送的消息（已过滤工具输出和系统内容）。
提取值得长期记住的稳定事实、偏好或纠正（如：居住地、习惯、喜欢/讨厌、工作流程、项目信息、明确纠正）。
规则：1) 只提取明确陈述，不推断、不脑补；2) 一次性/临时性内容不提取；3) 每条候选用用户原话片段（≤60字）；4) 宁缺毋滥，没有则输出空数组。
输出 JSON 数组，每项格式：{"type": "陈述|偏好|纠正", "text": "原话片段"}。
只输出 JSON，不要其他文字。
用户消息：
"""


def read_key() -> str:
    env = HERMES / ".env"
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DEEPSEEK_API_KEY not found")


def scan_new_msgs(watermark: float) -> tuple[list[str], float | None]:
    """扫一批（LIMIT MAX_MSGS）窗口内消息，返回 (消息列表, 本批最大 timestamp)。
    水位线必须用本批 max（不是全表 MAX），否则未扫描的消息会被跳过。"""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT content, timestamp FROM messages WHERE role='user' AND timestamp > ? "
            "AND content IS NOT NULL AND length(content) BETWEEN 8 AND 500 "
            "ORDER BY timestamp ASC LIMIT ?",
            (watermark, MAX_MSGS),
        )
        rows = cur.fetchall()
        msgs = [r[0].replace("\n", " ")[:200] for r in rows]
        max_ts = rows[-1][1] if rows else None
    finally:
        conn.close()
    return msgs, max_ts


def llm_extract(msgs: list[str]) -> list[dict]:
    key = read_key()
    body = json.dumps({
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": "\n".join(f"- {m}" for m in msgs)},
        ],
        "max_tokens": 800,
        "temperature": 0.1,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.load(r)
    content = d["choices"][0]["message"]["content"]
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        return []
    items = json.loads(m.group(0))
    return [i for i in items if isinstance(i, dict) and i.get("text")][:MAX_OUTPUT]


def main() -> int:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    wm = WATERMARK.read_text().strip() if WATERMARK.exists() else ""
    watermark = float(wm) if wm else (datetime.datetime.now().timestamp() - 86400)

    # 分批扫描：每批 MAX_MSGS 条，水位线逐批推进（本批 max，非全表 MAX），
    # 直到窗口耗尽或候选达到 MAX_OUTPUT。修复：旧版单批 30 条 + 全表 MAX
    # 推进会把未扫描消息永久吞掉（2026-08-29 实测 6 天零产出的根因）。
    total_items: list[dict] = []
    batch_count = 0
    while True:
        msgs, max_ts = scan_new_msgs(watermark)
        if not msgs or max_ts is None:
            break
        batch_count += 1
        print(f"扫描第 {batch_count} 批（{len(msgs)} 条，水位线 {datetime.datetime.fromtimestamp(watermark).isoformat()}）")
        try:
            items = llm_extract(msgs)
        except Exception as e:
            # 失败批不推进水位线，下轮 cron 从本批重扫（安全属性保持）
            print(f"⚠️ 第 {batch_count} 批 LLM 提取失败: {e}（水位线未推进，下轮重扫）")
            break
        total_items.extend(items)
        watermark = max_ts  # 本批已消费，水位线推进到本批末尾（即使本批无候选）
        if len(total_items) >= MAX_OUTPUT:
            break

    if not total_items:
        print(f"ℹ️ 无新增可提取候选（共扫 {batch_count} 批；水位线 {datetime.datetime.fromtimestamp(watermark).isoformat()}）")
    else:
        date = datetime.date.today().isoformat()
        out = PENDING_DIR / f"{date}.md"
        lines = [f"# 捕获候选 {date}", ""] if not out.exists() else []
        for it in total_items[:MAX_OUTPUT]:
            lines.append(f"- [{it.get('type', '候选')}] {it['text']}")
        with open(out, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"✅ 写入 {len(total_items[:MAX_OUTPUT])} 条候选 → {out}")

    WATERMARK.write_text(str(watermark))
    print(f"水位线更新: {datetime.datetime.fromtimestamp(watermark).isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
