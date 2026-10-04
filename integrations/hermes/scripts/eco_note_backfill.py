#!/usr/bin/env python3
"""经验笔记本 · 存量会话回扫（一次性补水工具，2026-09-03 用户批准）

用途：对高信号密度历史会话做一次性 LLM 提取，补足经验库存量（现有 45 条主要来自
8-07 首日会话，8-09 起的高密度富矿会话未覆盖——数据实证：833 精确信号 TOP12 占 60%）。

与生产管道的隔离（核心四件套零改动）：
- 不读/不写生产水位线（.watermark）——回扫是一次性动作，不参与增量扫描状态
- 候选写入 experiences/pending/backfill/<date>.md —— **子目录，adopt 不会自动接纳**
  （adopt 只扫 pending/<date>.md 顶层）；人工过目后把需要的候选移入顶层或直接评估
- 复用 eco_note.py 的 fetch_context / llm_extract（含 usage 记账，成本统一）
- 信号过滤：仅 tool_error（与精确判定一致）；同会话相邻信号按 CTX 窗口合并成簇（省 LLM 调用）

保守上限（防成本爆）：
  --max-cands 单会话最多候选数（默认 60；多会话运行总量会随之累加）
  --max-clusters-per-session 单会话最多处理簇数（默认 30）

用法:
  python eco_note_backfill.py --top 5                    # 自动取信号密度 TOP 5 会话
  python eco_note_backfill.py --sessions xxx,yyy --dry-run
  python eco_note_backfill.py --sessions xxx,yyy --max-cands 40
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import eco_note as en
import eco_note_signals as sig

BACKFILL_SUBDIR = "backfill"  # 候选子目录（adopt 不扫子目录 → 不会自动接纳）


def backfill_dir() -> Path:
    """动态解析（en.PENDING_DIR 可被测试/未来配置覆盖）。"""
    return en.PENDING_DIR / BACKFILL_SUBDIR
CTX_SPAN = en.CTX_BEFORE + en.CTX_AFTER + 2  # 相邻信号合并阈值（消息 id 差）


def top_sessions(n: int) -> list[str]:
    """按精确 tool_error 信号数取 TOP N 会话（不依赖 LIKE 粗筛）。"""
    import sqlite3
    conn = sqlite3.connect(f"file:{en.DB}?mode=ro", uri=True)
    rows = conn.execute("SELECT DISTINCT session_id FROM messages WHERE role='tool'").fetchall()
    conn.close()
    from collections import Counter
    c = Counter()
    # 复用精确判定（读整库较慢但一次即可）
    conn = sqlite3.connect(f"file:{en.DB}?mode=ro", uri=True)
    for (sid,) in rows:
        cur = conn.execute(
            "SELECT content FROM messages WHERE session_id=? AND role='tool'", (sid,))
        for (content,) in cur:
            if sig.is_tool_error(sig.parse_tool_content(content or "")):
                c[sid] += 1
    conn.close()
    return [s for s, _ in c.most_common(n)]


def collect_clusters(session_id: str, msg_ids: list[int], window: int) -> list[dict]:
    """信号 msg 按 id 窗口合并成簇（相邻信号共用上下文，防重复提取）。"""
    msg_ids = sorted(msg_ids)
    clusters: list[list[int]] = []
    for mid in msg_ids:
        if clusters and mid - clusters[-1][-1] <= window:
            clusters[-1].append(mid)
        else:
            clusters.append([mid])
    return [{"signal_msg_id": c[0], "size": len(c)} for c in clusters]


def session_errors(session_id: str) -> list[tuple[int, str]]:
    """某会话的精确错误信号（msg_id, content）。"""
    import sqlite3
    conn = sqlite3.connect(f"file:{en.DB}?mode=ro", uri=True)
    cur = conn.execute(
        "SELECT id, content FROM messages WHERE session_id=? AND role='tool' "
        "ORDER BY id ASC", (session_id,))
    out = []
    for mid, content in cur:
        if sig.is_tool_error(sig.parse_tool_content(content or "")):
            out.append((mid, content or ""))
    conn.close()
    return out


def load_processed() -> dict:
    """已处理簇记录（断点续传）。{session_id: [msg_id,...]}

    2026-09-03 并行化：改用按会话文件 .pc-<sid>.json（多 worker 并发写零冲突）；
    兼容旧版 .processed-clusters.json 的存量记录。
    """
    bd = backfill_dir()
    merged: dict[str, list[int]] = {}
    legacy = bd / ".processed-clusters.json"
    if legacy.exists():
        try:
            d = json.loads(legacy.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                merged = {k: list(v) for k, v in d.items() if isinstance(v, list)}
        except (OSError, ValueError):
            pass
    if bd.exists():
        for f in bd.glob(".pc-*.json"):
            sid = f.stem[len(".pc-"):]
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(d, list):
                    merged.setdefault(sid, [])
                    for m in d:
                        if m not in merged[sid]:
                            merged[sid].append(m)
            except (OSError, ValueError):
                continue
    return merged


def mark_processed(session_id: str, msg_id: int) -> None:
    """按会话写状态文件（原子；单会话只被单 worker 处理，无并发冲突）。"""
    bd = backfill_dir()
    bd.mkdir(parents=True, exist_ok=True)
    p = bd / f".pc-{session_id}.json"
    cur: list[int] = []
    if p.exists():
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(cur, list):
                cur = []
        except (OSError, ValueError):
            cur = []
    if msg_id not in cur:
        cur.append(msg_id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=True), encoding="utf-8")
    tmp.replace(p)


def load_seen_triggers() -> set[str]:
    """跨文件去重种子：读 backfill 目录已有候选的 trigger（防补跑重复落盘）。"""
    seen: set[str] = set()
    bd = backfill_dir()
    if not bd.exists():
        return seen
    for f in bd.glob("*.md"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^-\s*\[\w+\]\s*\*\*(.+?)\*\*", line)
            if m:
                seen.add(m.group(1))
    return seen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="经验库存量会话回扫（一次性）")
    ap.add_argument("--sessions", default="", help="逗号分隔会话 id（或 --top）")
    ap.add_argument("--top", type=int, default=0, help="自动取信号密度 TOP N 会话")
    ap.add_argument("--max-cands", type=int, default=60)
    ap.add_argument("--max-clusters-per-session", type=int, default=30)
    ap.add_argument("--max-tokens", type=int, default=8000,
                    help="LLM 提取初始 max_tokens（2026-09-03：reasoning 模型 2000 常被挤满触发重试 → 直接 8000）")
    ap.add_argument("--out", default=None,
                    help="输出候选文件（默认 backfill/<date>.md；并行模式用 cand-<session>.md 防写竞态）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.sessions:
        sessions = [s.strip() for s in args.sessions.split(",") if s.strip()]
    elif args.top:
        sessions = top_sessions(args.top)
    else:
        print("需指定 --sessions 或 --top")
        return 2
    print(f"回扫会话 {len(sessions)} 个: {', '.join(s[:20] for s in sessions)}")

    date = datetime.date.today().isoformat()
    out = Path(args.out) if args.out else backfill_dir() / f"{date}.md"
    if not args.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)

    total_cands = 0
    seen_triggers: set[str] = load_seen_triggers()
    processed = load_processed()
    for sid in sessions:
        errs = session_errors(sid)
        if not errs:
            print(f"  ∅ {sid[:20]} 无精确信号")
            continue
        done = set(processed.get(sid, []))
        clusters = [c for c in collect_clusters(sid, [m for m, _ in errs], CTX_SPAN)
                    if c["signal_msg_id"] not in done][: args.max_clusters_per_session]
        print(f"  📡 {sid[:20]}：{len(errs)} 信号 → {len(clusters)} 簇（已处理 {len(done)} 簇跳过）")
        session_cands = 0  # 每会话限额（2026-09-03：全局限额导致富矿会话被中途截断）
        for c in clusters:
            if session_cands >= args.max_cands:
                print(f"  ↔ {sid[:20]} 本会话候选达上限 {args.max_cands}（累计 {total_cands}），切下一会话")
                break
            frag = {"signal": {"type": "tool_error", "session_id": sid,
                               "msg_id": c["signal_msg_id"], "ts": 0.0, "snippet": ""},
                    # P1：与生产管道 eco_note.py 同口径——剥离经验注入回声行，
                    # 防止已入库经验被回扫重新提取（自我污染循环）
                    "ctx": en.strip_echo_lines(
                        en.fetch_context(sid, c["signal_msg_id"], en.CTX_BEFORE, en.CTX_AFTER))}
            if not frag["ctx"]:
                # OCR 2026-10-03: mark_processed 移入非 dry-run 分支——dry-run 不留断点痕迹
                if not args.dry_run:
                    mark_processed(sid, c["signal_msg_id"])
                continue
            if args.dry_run:
                session_cands += 1
                total_cands += 1
                continue
            try:
                items = en.llm_extract(frag["ctx"], max_tokens=args.max_tokens)
            except Exception as e:
                # P1：提取失败不标记已处理——对齐 eco_note.py「异常≠无经验、不推进」语义，
                # 断点续扫下轮重试（旧版 mark_processed 把瞬态故障变成永久静默跳过）
                print(f"    ⚠️ 簇 {c['signal_msg_id']} 提取失败: {e}（不标记，下轮重试）")
                continue
            got = 0
            for it in items:
                # OCR 2026-10-03: 先截断到落盘长度再做去重键——内存键与持久化键保持一致，
                # 否则 >60 字符的 trigger 断点续跑时必然重复落盘
                t = (it.get("trigger") or "").replace("\n", " ").replace("**", "∗∗")[:60]
                if not t or t in seen_triggers:
                    continue
                seen_triggers.add(t)
                line = (f"- [{it.get('type','?')}] **{t[:60]}**\n"
                        f"    - symptom: {it.get('symptom','')}\n"
                        f"    - cause: {it.get('cause','')}\n"
                        f"    - action: {it.get('action','')}\n"
                        f"    - evidence: {it.get('evidence','')}\n"
                        f"    - boundary: {it.get('boundary','')}\n"
                        f"    - 来源信号: backfill / session {sid}\n")
                with open(out, "a", encoding="utf-8") as fh:
                    fh.write(line)
                got += 1
                session_cands += 1
                total_cands += 1
            print(f"    ✓ 簇 {c['signal_msg_id']} → {got} 条候选")
    print(f"🏁 完成：{'dry-run 预览' if args.dry_run else '回扫写入'} {total_cands} 条候选 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
