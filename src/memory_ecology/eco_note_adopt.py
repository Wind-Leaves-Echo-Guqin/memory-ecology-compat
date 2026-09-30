#!/usr/bin/env python3
"""经验笔记本 · 候选接纳层（正式版 v1.0 最小实现）

pending/<date>.md 候选 → experiences/exp-<id>.md 正式条目。

规则（设计稿 §2.2 接纳分级，L0/L1 自动可回滚）：
  - 自动接纳：provenance 为 agent 源（默认）+ 无过期标记的候选
  - 污染降级：type/label 含外部来源特征 → 标 draft 暂缓（人工确认）
  - 强制人工：provenance=external/toolout（设计稿 §1.5 污染预检）

正式条目 frontmatter（设计稿 §1.2）：
  id / type / status / created / last_hit / provenance / source / distilled_to
正文：trigger / symptom / cause / action / evidence / boundary / note

被接纳条目从 pending 文件标记（→ .accepted-<ts> 行前缀），不删除候选源
（用户习惯：备份文件绝不删除）。可用 --dry-run 预览。

用法: python eco_note_adopt.py [--dry-run]
"""
from __future__ import annotations

import re
import sys
from datetime import date as _date, datetime
from pathlib import Path

from lib.config import hermes_root
from lib import safeio

HERMES = hermes_root()
EXP_DIR = HERMES / "experiences"
PENDING_DIR = EXP_DIR / "pending"

# 污染预检（设计稿 §1.5）：外源/工具输出特征 → 降级 draft
POLLUTION_MARKERS = ("外部内容", "网页内容", "untrusted", "API 响应", "web 结果", "网络内容")


def parse_pending_md(path: Path) -> list[dict]:
    """解析 pending 候选文件为候选 dict 列表。行格式：
    - [type] **trigger**
        - symptom: ...
        - cause: ...
        - action: ...
        - evidence: ... | boundary: ...
        - 来源信号: <sigtype> / session <sid>
    """
    cands: list[dict] = []
    cur: dict | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^-\s*\[(\w+)\]\s*\*\*(.+?)\*\*", line)
        if m:
            cur = {"type": m.group(1), "trigger": m.group(2), "fields": {}}
            cands.append(cur)
            continue
        if cur is not None:
            fm = re.match(r"^\s{4}- (symptom|cause|action|evidence|boundary|note): (.*)$", line)
            if fm:
                key, val = fm.group(1), fm.group(2)
                # v1.1-5 兼容旧格式（2026-09-02）：v1.0 曾把 boundary 拼在 evidence 同行
                # （`evidence: X | boundary: Y`，正则捕获整行 → 45 条 boundary 并入 evidence）。
                # 新格式已独立成行；此处拆分旧行保证回读旧 pending 文件仍正确。
                if key == "evidence" and " | boundary: " in val:
                    ev, bd = val.split(" | boundary: ", 1)
                    cur["fields"]["evidence"] = ev
                    cur["fields"]["boundary"] = bd
                else:
                    cur["fields"][key] = val
            sm = re.match(r"^\s{4}- 来源信号: (\w+) / session (\S+)", line)
            if sm:
                cur["signal_type"] = sm.group(1)
                cur["session"] = sm.group(2)
    return cands


# 稳定 id 生成（v1.1-6，2026-09-02 双审采纳）：日期 + 当日序号。
# 弃 abs(hash(trigger))%10000 —— str hash 跨进程随机，同 trigger 二次接纳会产出不同 id。
# 序号从 0001 起，跳过既有 exp-<date>-* 文件占用的数字（含旧 hash 格式，防碰撞）。
def make_id(cands_existing: set[str], date: str, trigger: str) -> str:
    prefix = f"exp-{date.replace('-', '')}-"
    used: set[int] = set()
    for eid in cands_existing:
        m = re.match(rf"^{re.escape(prefix)}(\d{{4}})", eid)
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"{prefix}{n:04d}"


def build_entry(c: dict, eid: str) -> str:
    now = datetime.now().isoformat(timespec="seconds")
    prov = "agent" if not any(m in (c.get("trigger", "") + " ".join(c.get("fields", {}).values())) for m in POLLUTION_MARKERS) else "external"
    status = "draft" if prov == "external" else "draft"  # 正式版首态一律 draft（证据登记后才 verified）
    f = c.get("fields", {})
    lines = [
        "---",
        f"id: {eid}",
        f"type: {c.get('type', '?')}",
        f"status: {status}",
        f"created: {now}",
        f"last_hit: {now[:10]}",
        f"provenance: {prov}",
        f"source: {c.get('session', '')} | {c.get('signal_type', '')}",
        "distilled_to: ''",
        "---",
        "",
        f"title: {c.get('trigger', '')}",
        "",
    ]
    for k in ("symptom", "cause", "action", "evidence", "boundary", "note"):
        v = f.get(k)
        if v:
            lines.append(f"{k}: {v}")
    return "\n".join(lines) + "\n"


def mark_accepted(pending_path: Path, trigger: str) -> None:
    """在 pending 文件的行首标 *.accepted*（不删除源行）。"""
    text = pending_path.read_text(encoding="utf-8")
    out_lines = []
    for line in text.splitlines():
        if line.startswith(f"- [") and f"**{trigger}**" in line:
            out_lines.append(f"<!-- accepted {datetime.now().isoformat(timespec='seconds')} -->" + line)
        else:
            out_lines.append(line)
    from lib.fs import atomic_write
    atomic_write(pending_path, "\n".join(out_lines) + "\n")


def main() -> int:
    dry = "--dry-run" in sys.argv
    existing = {p.stem for p in EXP_DIR.glob("exp-*.md")}
    date = _date.today().isoformat()

    # 2026-09-27 修复（实战发现）：原实现只处理 PENDING_DIR/<今天>.md——
    # 历史积压（本次实测 09-04 起 22 批）永远不会被接纳，管道看似"在跑"实则只吞当天。
    # 改为：处理全部未接纳的 pending 文件（按文件名序=日期序），每文件各自日期生成 id。
    all_pending = sorted(
        p for p in PENDING_DIR.glob("*.md")
        if p.name not in ("README.md", ".watermark")
        and not p.name.endswith((".done.md", ".rejected.md", ".accepted.md"))
        and p.name != ".watermark.md"
    )
    if not all_pending:
        print("ℹ️ 无候选文件，无需接纳")
        return 0

    accepted = 0
    rejected = 0
    for pending in all_pending:
        file_date = pending.stem if re.match(r"^\d{4}-\d{2}-\d{2}$", pending.stem) else date
        cands = parse_pending_md(pending)
        untouched = [c for c in cands if "." not in c.get("trigger", "")]
        # 只接纳本文件里未标记的条目（已 accepted 的行由 parse_pending_md 原样读到，
        # 但那些行有 <!-- accepted ... --> 前缀 → trigger 会带 '.'，据此跳过）
        fresh = [c for c in cands
                 if not _is_accepted_line(pending, c.get("trigger", ""))]
        if not fresh:
            continue
        if len(all_pending) > 1:
            print(f"── {pending.name}（{len(fresh)} 条未接纳）──")
        for c in fresh:
            eid = make_id(existing, file_date, c.get("trigger", ""))
            existing.add(eid)
            content = build_entry(c, eid)
            if dry:
                print(f"  -- 将接纳 [{c.get('type')}] {c.get('trigger', '')[:40]} → {eid}.md")
                continue
            try:
                # v2.2.0（R1）：per-candidate 异常隔离——单条坏候选不再毒死整条接纳管道
                safeio.write_entry(safeio.safe_entry_path(EXP_DIR, eid), content, kind="experience")
                mark_accepted(pending, c.get("trigger", ""))
                accepted += 1
                print(f"  ✅ 接纳 [{eid}] {c.get('type')} {c.get('trigger', '')[:40]}")
            except (ValueError, OSError) as e:
                rejected += 1
                print(f"  ⚠️ 拒纳 [{eid}] {c.get('trigger', '')[:40]}: {e}")
    if not dry:
        extra = f"，拒纳 {rejected} 条" if rejected else ""
        print(f"🏁 接纳完成：{accepted} 条{extra}（源 pending 已标记保留）")
    _ = untouched  # 保留：未来若需「未标记统计」直接用
    return 0


def _is_accepted_line(pending_path: Path, trigger: str) -> bool:
    """该 trigger 所在行是否已被标记接纳（<!-- accepted ... --> 前缀）。"""
    if not trigger:
        return False
    try:
        for line in pending_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if trigger in line and line.lstrip().startswith("<!--"):
                return True
    except OSError:
        pass
    return False


if __name__ == "__main__":
    sys.exit(main())
