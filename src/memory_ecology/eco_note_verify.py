#!/usr/bin/env python3
"""经验笔记本 · 证据登记命令（v1.1-3b，2026-09-02 双审采纳）

受控地把条目 status: draft → verified（设计稿 §1.3 状态机：权威证据登记后转 verified）。
- 证据登记 = 权威通道（用户一句话 / 可复现验证），非自动判定——补「状态机无写入方」缺陷
- 登记同时更新 last_hit（设计 §5.3 回顾窗口：验证 = 一次确定命中）

用法: python eco_note_verify.py <id> [--evidence "文本"] [--dry-run]
  <id>：全名（exp-20260902-0236）或短尾（0236，须唯一匹配）
  --evidence：提供时更新正文 evidence 行（替换首处或末尾追加）

登记后建议运行 python eco_note_index.py 重建 INDEX（status 列刷新）。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from lib.config import hermes_root

HERMES = hermes_root()
EXP_DIR = HERMES / "experiences"


def find_entry(exp_dir: Path, ident: str) -> Path | None:
    """按全名或短尾定位条目文件；短尾须唯一匹配（多命中返回 None 并打印提示）。"""
    if re.fullmatch(r"exp-\d{8}-\d{4}(-\d+)?", ident):
        p = exp_dir / f"{ident}.md"
        return p if p.exists() else None
    hits = list(exp_dir.glob(f"exp-*{ident}*.md"))
    if len(hits) > 1:
        print(f"⚠️ 短尾 {ident} 命中 {len(hits)} 个条目，请用全名: " + ", ".join(h.stem for h in hits))
        return None
    return hits[0] if hits else None


def verify_entry(path: Path, evidence: str | None = None, dry: bool = False) -> tuple[bool, str]:
    """将条目 status 置 verified（含 last_verified/last_hit 更新，写前备份，原子写）。

    返回 (成功, 说明)。已 verified 的条目不动（幂等拒绝，防重复登记）。
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return False, f"非法条目（无 frontmatter）: {path.name}"
    if re.search(r"^status:\s*verified", text, re.M):
        return False, f"{path.name} 已是 verified，无需登记"

    now = datetime.now().isoformat(timespec="seconds")
    today = now[:10]
    out: list[str] = []
    replaced_status = False
    seen_hit = False
    # has_lv 只看 frontmatter 块——正文里以 last_verified: 开头的行不该抑制插入
    _fm_end = text.find("\n---", 3)
    _fm_block = text[3:_fm_end] if _fm_end > 0 else text[3:]
    has_lv = re.search(r"^last_verified:", _fm_block, re.M) is not None
    for line in text.splitlines():
        if line.startswith("status:") and not replaced_status:
            out.append("status: verified")
            if not has_lv:
                # 条目原本没有 last_verified 行才随 status 插入；
                # 有则由下方分支原位替换——否则新旧两行并存（P0-6）
                out.append(f"last_verified: {today}")
            replaced_status = True
        elif line.startswith("last_hit:"):
            out.append(f"last_hit: {today}")
            seen_hit = True
        elif line.startswith("last_verified:"):
            out.append(f"last_verified: {today}")
        else:
            out.append(line)
    if not replaced_status:
        return False, f"{path.name} frontmatter 无 status 行"
    body = "\n".join(out)
    if not seen_hit and not re.search(r"^last_hit:", body, re.M):
        # 极老条目缺 last_hit 行 → 在 last_verified 后补（保持前 10 字符=日期约定）
        body = re.sub(r"^last_verified: (\S+)$", r"last_verified: \1\nlast_hit: \1", body, count=1, flags=re.M)
    if evidence is not None:
        if re.search(r"^evidence:.*$", body, re.M):
            body = re.sub(r"^evidence:.*$", f"evidence: {evidence}", body, count=1, flags=re.M)
        else:
            body = body.rstrip("\n") + f"\nevidence: {evidence}\n"
    if dry:
        return True, f"（dry-run）将登记 {path.name} → verified（evidence={evidence!r}）"
    bak = path.with_name(f"{path.name}.bak-verify-{int(datetime.now().timestamp())}")
    bak.write_text(text, encoding="utf-8")
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)
    note = f"证据已更新" if evidence is not None else "未提供证据文本"
    return True, f"✅ {path.name} → verified（last_verified={today}，{note}；备份 {bak.name}）"


def main() -> int:
    ap = argparse.ArgumentParser(description="经验条目证据登记（draft→verified）")
    ap.add_argument("ident", help="条目 id（全名或唯一短尾）")
    ap.add_argument("--evidence", default=None, help="权威证据文本（可选）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    path = find_entry(EXP_DIR, args.ident)
    if path is None:
        print(f"未找到条目: {args.ident}（全名形如 exp-20260902-0236）")
        return 1
    ok, msg = verify_entry(path, evidence=args.evidence, dry=args.dry_run)
    print(msg)
    print("提示: python eco_note_index.py 重建 INDEX 刷新 status 列" if ok and not args.dry_run else "")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
