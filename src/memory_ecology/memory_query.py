#!/usr/bin/env python3
"""记忆查询门 memory_query（P0.5 需求链路入口，2026-10-04）。

四道门治理"供给"（写入/配额/蒸馏/复核），本模块补上缺失的"需求"侧：
此前 memories/detail+archive 没有任何查询工具——L1 恒在上下文是唯一通道，
agent"找信息"只能靠宿主 grep 碰运气，休眠因此沦为日历驱动的延迟归档。

功能：
1) 打分检索 detail/ + archive/（字段加权：name 命中 > 正文命中；词频加成；
   近期命中加成；状态降权——active < dormant < superseded < archived，
   级联思想复用 eco_search 的 STATUS_RANK）。空格分隔多词 = AND。
2) 命中遥测：每次查询追加 memories/.memory_hits.jsonl 一行（query → 命中 slugs），
   供门④"使用驱动休眠"与月度仪式取数（--no-log 可关闭）。
3) 命中回写：默认把 last_hit=今天 / use_count+1 写回命中条目 frontmatter
   （--no-update 关闭）——被用过的记忆从此免于日历驱动的降级/归档。
4) 复活提案与执行：命中 dormant/superseded/archived 条目时写入
   memories/gate_log/revive-<date>.md（按 slug 去重）；--revive <slug> 为人工
   勾选后的执行入口（dormant/superseded → status=active + last_verified=今天；
   archived → 移回 detail 再激活），写 review_log 留痕。半自动纪律：只提案，不自动复活。

检索语义（如实声明）：字符级子串匹配 + 词面打分，非语义检索——
召回依赖关键词选择；规模前提：<1000 条无需向量库（触发线见 ARCHITECTURE_TODO）。

用法:
  python memory_query.py <关键词> [--top N] [--format text|json]
                         [--no-log] [--no-update] [--revive [SLUG]]
返回码: 0=有命中（或 revive 成功），1=无命中/参数错误。
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

from lib.config import hermes_root
from lib.fs import atomic_write, norm
from lib.gatekit import connect_db, write_ledger
from lib.memstore import clean_value, parse_frontmatter_strict
from eco_review import rewrite_status  # §C：status 重写口径单源（含 last_verified 补行）

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERMES = hermes_root()
DETAIL_DIR = HERMES / "memories" / "detail"
ARCHIVE_DIR = HERMES / "memories" / "archive"
HITS_LOG = HERMES / "memories" / ".memory_hits.jsonl"
GATE_LOG_DIR = HERMES / "memories" / "gate_log"
DB = HERMES / "eco.db"

TODAY = datetime.date.today()

# 状态降权（越小越靠前）：与 eco_search.STATUS_RANK 同思想，范围扩到 detail 的实际状态域
STATUS_PENALTY = {
    "active": 0, "candidate": 2, "undeclared": 8, "unknown": 8,
    "dormant": 10, "frozen": 12, "superseded": 14, "archived": 15,
}


def parse_date(s: str) -> datetime.date | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def scan_memory_dirs(detail_dir: Path = DETAIL_DIR, archive_dir: Path = ARCHIVE_DIR) -> list[dict]:
    """扫描 detail（平铺）+ archive（含日期子目录），返回条目列表（只读）。"""
    entries: list[dict] = []

    def _add(p: Path, source: str) -> None:
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        fields, body = parse_frontmatter_strict(raw)
        if fields is None:
            return
        entries.append({
            "path": p, "source": source,
            "slug": clean_value(fields.get("name")) or p.stem,
            "type": (clean_value(fields.get("type")) or "unknown").lower(),
            "status": (clean_value(fields.get("status")) or "unknown").lower(),
            "body": body,
            "last_seen": clean_value(fields.get("last_seen")),
            "last_hit": clean_value(fields.get("last_hit")),
            "use_count": clean_value(fields.get("use_count")),
            "raw": raw,
        })

    if detail_dir.is_dir():
        for p in sorted(detail_dir.glob("*.md")):
            _add(p, "detail")
    if archive_dir.is_dir():
        for p in sorted(archive_dir.rglob("*.md")):
            _add(p, "archive")
    return entries


def score_entry(terms: list[str], e: dict) -> int | None:
    """打分：None = 不命中。全部 term 须命中（name/type/body 合并文本，AND 语义）。"""
    name_n = norm(e["slug"])
    type_n = norm(e["type"])
    body_n = norm(e["body"])
    hit_terms = 0
    base = 0
    for t in terms:
        if not t:
            continue
        if t in name_n:
            base += 100  # 名字命中 = 最强意图
            hit_terms += 1
        elif t in body_n:
            base += 60
            hit_terms += 1
        elif t in type_n:
            base += 20
            hit_terms += 1
    if hit_terms < len([t for t in terms if t]):
        return None  # 有 term 未命中 → AND 语义下不返回
    # 词频加成（首 term 在正文的出现次数，封顶防单词刷分）
    if terms:
        base += min(body_n.count(terms[0]), 5) * 4
    # 近期命中/出现加成（需求信号与新鲜度）
    today = datetime.date.today()
    for field in ("last_hit", "last_seen"):
        d = parse_date(e.get(field) or "")
        if d is not None:
            age = (today - d).days
            if age <= 7:
                base += 10
            elif age <= 30:
                base += 5
            break
    # 状态/来源罚分从奖励分中扣减（总分越高越靠前，降序）
    base -= STATUS_PENALTY.get(e["status"], 8)
    if e["source"] == "archive":
        base -= 5  # 归档区整体再降一档（可查但靠后）
    return base


def bump_hit(e: dict) -> bool:
    """命中回写：last_hit=今天、use_count+1（frontmatter 原位改，原子写）。失败返回 False。"""
    raw = e["raw"]
    lines = raw.splitlines(keepends=True)
    today = TODAY.isoformat()
    fence = 0
    lh_done = False
    uc_done = False
    for i, ln in enumerate(lines):
        stripped = ln.rstrip("\r\n").strip()
        if fence < 2 and stripped == "---":
            fence += 1
            continue
        if fence != 1 or ":" not in stripped:
            continue
        key = stripped.split(":", 1)[0].strip()
        indent = ln[:len(ln) - len(ln.lstrip())]
        ending = ln[len(ln.rstrip("\r\n")):] or "\n"
        if key == "last_hit" and not lh_done:
            lines[i] = f"{indent}last_hit: {today}{ending}"
            lh_done = True
        elif key == "use_count" and not uc_done:
            try:
                n = int(stripped.split(":", 1)[1].strip() or "0")
            except ValueError:
                n = 0
            lines[i] = f"{indent}use_count: {n + 1}{ending}"
            uc_done = True
    if not lh_done or not uc_done:
        # 缺行则在 frontmatter 末尾（第二个 --- 前）补
        insert_at = None
        seen_fence = 0
        for i, ln in enumerate(lines):
            if ln.rstrip("\r\n").strip() == "---":
                seen_fence += 1
                if seen_fence == 2:
                    insert_at = i
                    break
        add = []
        if not lh_done:
            add.append(f"last_hit: {today}\n")
        if not uc_done:
            add.append("use_count: 1\n")
        if insert_at is not None:
            lines[insert_at:insert_at] = add
    try:
        atomic_write(e["path"], "".join(lines))
        e["raw"] = "".join(lines)
        return True
    except OSError:
        return False


def log_hits(query: str, hits: list[dict]) -> None:
    """命中遥测：一行 JSON（ts/query/hits）。失败静默（遥测不阻塞查询）。"""
    try:
        HITS_LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "query": query, "hits": [h["slug"] for h in hits]}
        with HITS_LOG.open("a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def propose_revive(hits: list[dict]) -> int:
    """复活提案：命中 dormant/superseded/archived → 写 revive-<date>.md（按 slug 去重）。"""
    need = [h for h in hits if h["status"] in ("dormant", "superseded", "archived", "frozen")]
    if not need:
        return 0
    try:
        GATE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        rf = GATE_LOG_DIR / f"revive-{TODAY.isoformat()}.md"
        existing = set()
        if rf.exists():
            existing = {ln.split("|")[1].strip() for ln in rf.read_text(encoding="utf-8").splitlines()
                        if ln.startswith("| ") and len(ln.split("|")) > 2
                        and ln.split("|")[1].strip() != "slug"}
        new = [h for h in need if h["slug"] not in existing]
        if not new:
            return 0
        if not rf.exists():
            rf.write_text(f"# 复活候选 {TODAY.isoformat()}（memory_query 命中产生；人工确认后执行"
                          f" `python memory_query.py --revive <slug>`）\n\n"
                          f"| slug | status | 来源 | 命中原因 |\n|---|---|---|---|\n", encoding="utf-8")
        with rf.open("a", encoding="utf-8", newline="") as f:
            for h in new:
                f.write(f"| {h['slug']} | {h['status']} | {h['source']} | 检索命中 |\n")
        return len(new)
    except OSError:
        return 0


def revive(slug: str, detail_dir: Path = DETAIL_DIR, archive_dir: Path = ARCHIVE_DIR,
           db: Path = DB) -> tuple[bool, str]:
    """人工确认后的复活执行：dormant/superseded → active（原地）；
    archived → 移回 detail 再 active。写 review_log 留痕。"""
    target = detail_dir / f"{slug}.md"
    from_archive = False
    if not target.exists():
        cands = [p for p in archive_dir.rglob(f"{slug}.md")] if archive_dir.is_dir() else []
        if not cands:
            return False, f"detail/archive 均未找到 {slug}.md"
        target = cands[0]
        from_archive = True
    raw = target.read_text(encoding="utf-8", errors="replace")
    if from_archive:
        # 先移回 detail（重名加后缀），再激活
        dest = detail_dir / target.name
        n = 2
        while dest.exists():
            dest = detail_dir / f"{target.stem}-{n}{target.suffix}"
            n += 1
        detail_dir.mkdir(parents=True, exist_ok=True)
        target.rename(dest)
        target = dest
        raw = target.read_text(encoding="utf-8", errors="replace")
    new_content, changed = rewrite_status(raw, "active", refresh_lv=True)
    if not changed:
        return False, f"{target.name} frontmatter 无 status 行，未修改"
    atomic_write(target, new_content)
    try:
        conn = connect_db(db)
        try:
            write_ledger(conn, "review_log",
                         [(datetime.datetime.now().isoformat(timespec="seconds"),
                           "revive", slug, f"人工确认复活（{'archive→detail' if from_archive else '原地'}）")],
                         ["ts", "action", "slug", "reason"])
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        return True, f"✅ {slug} 已复活（账本写入失败: {e}）"
    return True, f"✅ {slug} 已复活（{'archive→detail' if from_archive else '原地'}，status=active，last_verified=今天）"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="记忆查询门（detail+archive 打分检索，只读+遥测）")
    ap.add_argument("keyword", nargs="?", help="检索关键词（子串匹配，空格分隔多词=AND）")
    ap.add_argument("--top", type=int, default=5, help="最多返回条数（默认 5）")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--no-log", action="store_true", help="不写命中遥测")
    ap.add_argument("--no-update", action="store_true", help="不回写 last_hit/use_count")
    ap.add_argument("--revive", nargs="?", const="__list__", default=None, metavar="SLUG",
                    help="无参数：列出今日复活候选；带 slug：执行复活（人工确认后使用）")
    args = ap.parse_args(argv)

    if args.revive is not None:
        if args.revive == "__list__":
            rf = GATE_LOG_DIR / f"revive-{TODAY.isoformat()}.md"
            if rf.exists():
                print(rf.read_text(encoding="utf-8"))
                return 0
            print(f"今日（{TODAY.isoformat()}）暂无复活候选")
            return 1
        ok, msg = revive(args.revive)
        print(msg)
        return 0 if ok else 1

    if not args.keyword:
        ap.error("缺少检索关键词（或使用 --revive）")
        return 1

    terms = [norm(t) for t in args.keyword.split() if norm(t)]
    entries = scan_memory_dirs()
    scored = []
    for e in entries:
        s = score_entry(terms, e)
        if s is not None:
            scored.append((s, e))
    scored.sort(key=lambda t: (-t[0], t[1]["slug"]))  # 总分降序（奖励−罚分，越高越靠前）
    hits = [{"score": s, **e} for s, e in scored[:args.top]]

    if args.no_update:
        updated = 0
    else:
        updated = sum(1 for h in hits if bump_hit(h))
    if not args.no_log:
        log_hits(args.keyword, hits)
    proposed = propose_revive(hits)

    if args.format == "json":
        print(json.dumps({
            "ok": bool(hits), "query": args.keyword, "total": len(scored),
            "updated": updated, "revive_proposed": proposed,
            "hits": [{"id": h["slug"], "score": h["score"], "status": h["status"],
                      "type": h["type"], "source": h["source"],
                      "last_hit": h["last_hit"] or "",
                      "preview": h["body"][:60]} for h in hits],
        }, ensure_ascii=False, indent=2))
        return 0 if hits else 1

    if not hits:
        print(f"未命中：{args.keyword}（扫描 {len(entries)} 条，detail+archive）")
        return 1
    print(f"命中 {len(hits)}/{len(entries)} 条（状态越靠后越降权；归档区可查但靠后）:")
    for h in hits:
        print(f"  [{h['score']:>3}] {h['slug']}  [{h['status']}/{h['type']}] ({h['source']})")
        print(f"        {h['body'][:80]}")
    if proposed:
        print(f"  💡 {proposed} 条休眠/归档条目被命中 → 已写入复活候选（gate_log/revive-{TODAY.isoformat()}.md），"
              f"人工确认后执行 --revive <slug>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
