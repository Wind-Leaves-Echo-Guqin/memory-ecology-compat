#!/usr/bin/env python3
"""记忆查询门 memory_query（P0.5 需求链路入口，2026-10-04）。

四道门治理"供给"（写入/配额/蒸馏/复核），本模块补上缺失的"需求"侧：
此前 memories/detail+archive 没有任何查询工具——L1 恒在上下文是唯一通道，
agent"找信息"只能靠宿主 grep 碰运气，休眠因此沦为日历驱动的延迟归档。

功能：
1) 打分检索 detail/ + archive/（字段加权：name 命中 > 正文命中；词频加成；
   时间衰减（v2.3.0）；状态降权——active < dormant < superseded < archived，
   级联思想复用 eco_search 的 STATUS_RANK）。空格分隔多词 = AND。
2) 时间维过滤（v2.3.0）：--since/--until/--window 按「发生时间」过滤
   （valid_time → transaction_time → first_seen 回退链；无时态条目在启用
   时间过滤时跳过并在输出中计数——与 Hindsight 的 time-window 语义一致：
   时间窗查询只匹配有时间的记忆）。
3) 命中遥测：每次查询追加 memories/.memory_hits.jsonl 一行（query → 命中 slugs），
   供门④"使用驱动休眠"、零结果率观测（eco_retrieval_report）与月度仪式取数
   （--no-log 可关闭；零结果同样落行）。
4) 命中回写：默认把 last_hit=今天 / use_count+1 写回命中条目 frontmatter
   （--no-update 关闭）——被用过的记忆从此免于日历驱动的降级/归档。
5) OR 召回（v2.4.0 S7，--recall）：多词查询在 AND 全中之外，额外召回命中任一 term 的条目
   （中文常用词多为 2 字，词序/搭配不同时 AND 常全灭——实测「记忆 检索」AND=0 而 OR=5），
   与原 AND 结果做 RRF 融合排序（AND 命中双份票在前，OR 独有补位）；默认关闭=行为锁定。
6) 复活提案与执行：命中 dormant/superseded/archived 条目时写入
   memories/gate_log/revive-<date>.md（按 slug 去重）；--revive <slug> 为人工
   勾选后的执行入口（dormant/superseded → status=active + last_verified=今天；
   archived → 移回 detail 再激活），写 review_log 留痕。半自动纪律：只提案，不自动复活。

检索语义（如实声明）：字符级子串匹配 + 词面打分，非语义检索——
召回依赖关键词选择；规模前提：<1000 条无需向量库（触发线见 ARCHITECTURE_TODO）。
时间语义：valid_time≈发生时间（occurred_at）、transaction_time≈得知时间（learned_at）
——Graphiti 双时态轻量版在检索侧的首次消费（v2.3.0）。
第二路选型（v2.4.0 实测记录）：FTS5 三配置均不适合中文——porter 是英文词干器（中文零收益）、
trigram 要求查询 ≥3 字符（中文常用词恰 2 字，「备份」「框架」全零）、unicode61 与现有子串路
高度重合。故第二路用零依赖 OR 召回（--recall）；FTS5 登记为 >1000 条的规模化路径。

用法:
  python memory_query.py <关键词> [--top N] [--format text|json]
                         [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--window DAYS]
                         [--semantic] [--recall] [--no-log] [--no-update] [--revive [SLUG]]
返回码: 0=有命中（或 revive 成功），1=无命中/参数错误。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from lib.config import hermes_root
from lib.fs import atomic_write, norm
from lib.gatekit import connect_db, write_ledger
from lib.memstore import clean_value, parse_frontmatter, parse_frontmatter_strict, dump_frontmatter
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
# 向量缓存路径（shadow index，可丢弃）：MEMORY_ECOLOGY_VECTOR_CACHE 可覆盖（缓存可另置/测试隔离）
VECTOR_CACHE_DB = Path(os.environ.get("MEMORY_ECOLOGY_VECTOR_CACHE") or (HERMES / "memories" / ".vector_cache.db"))

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


def event_date(e: dict) -> datetime.date | None:
    """条目「发生时间」：valid_time → transaction_time → first_seen 回退链；无则 None。"""
    for field in ("valid_time", "transaction_time", "first_seen"):
        d = parse_date(e.get(field) or "")
        if d is not None:
            return d
    return None


def scan_memory_dirs(detail_dir: Path | None = None,
                     archive_dir: Path | None = None) -> list[dict]:
    """扫描 detail（平铺）+ archive（含日期子目录），返回条目列表（只读）。

    默认参数用 None 占位、调用时再取模块属性——避免 def 时绑定导致测试
    patch mq.DETAIL_DIR/ARCHIVE_DIR 失效（v2.3.0 修复）。"""
    detail_dir = detail_dir if detail_dir is not None else DETAIL_DIR
    archive_dir = archive_dir if archive_dir is not None else ARCHIVE_DIR
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
            "valid_time": clean_value(fields.get("valid_time")),
            "transaction_time": clean_value(fields.get("transaction_time")),
            "first_seen": clean_value(fields.get("first_seen")),
            "superseded_by": clean_value(fields.get("superseded_by")),
            "superseded_reason": clean_value(fields.get("superseded_reason")),
            "superseded_at": clean_value(fields.get("superseded_at")),
            "evidence": clean_value(fields.get("evidence")),
            "distilled_at": clean_value(fields.get("distilled_at")),
            "raw": raw,
        })

    if detail_dir.is_dir():
        for p in sorted(detail_dir.glob("*.md")):
            _add(p, "detail")
    if archive_dir.is_dir():
        for p in sorted(archive_dir.rglob("*.md")):
            _add(p, "archive")
    return entries


def time_bonus(e: dict) -> int:
    """时间衰减加成（score_entry 与 --semantic 向量路共用；v2.3.0 三档半连续）。

    回退链 last_hit→last_seen→valid_time→first_seen（旧格式条目无 last_hit 也能参与）；
    ≤7 天 +10 / ≤30 天 +5（既有操作点不动）；31–120 天线性 5→0（平缓新鲜度带，
    消除 30 天处的硬悬崖）；>120 天 0。未来日期按 age≤7 处理。
    """
    today = datetime.date.today()
    for field in ("last_hit", "last_seen", "valid_time", "first_seen"):
        d = parse_date(e.get(field) or "")
        if d is not None:
            age = (today - d).days
            if age <= 7:
                return 10
            if age <= 30:
                return 5
            if age <= 120:
                return int(round(5 * (120 - age) / 90))
            return 0
    return 0


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
    # 时间衰减（需求信号与新鲜度；三档半连续，语义见 time_bonus）
    base += time_bonus(e)
    # 状态/来源罚分从奖励分中扣减（总分越高越靠前，降序）
    base -= STATUS_PENALTY.get(e["status"], 8)
    if e["source"] == "archive":
        base -= 5  # 归档区整体再降一档（可查但靠后）
    return base


def or_recall(terms: list[str], entries: list[dict]) -> list[tuple[int, dict]]:
    """OR 召回兜底（v2.4.0 S7）：任一 term 命中即入候选，按命中强度+命中项数打分。

    背景（2026-10-07 实测）：多词 AND 在词序/搭配不同时全灭——「记忆 检索」AND=0
    而 OR=5、「备份 判据」AND=1 而 OR=8。中文查询词序自由、无空格分词惯例，
    AND 过严是词面检索的最大召回缺口。本函数只做召回不设门槛，与 AND 路经
    rrf_fuse 融合后由融合名次排序（不直接比分数——两路打分口径不同）。
    """
    out = []
    for e in entries:
        name_n = norm(e["slug"])
        type_n = norm(e["type"])
        body_n = norm(e["body"])
        base = 0
        matched = 0
        for t in terms:
            if not t:
                continue
            if t in name_n:
                base += 100  # 名字命中 = 最强意图（与 score_entry 同口径）
                matched += 1
            elif t in body_n:
                base += 60
                matched += 1
            elif t in type_n:
                base += 20
                matched += 1
        if matched == 0:
            continue
        base += matched * 10  # OR 路核心信号：命中项数越多越可能相关
        base += time_bonus(e)
        base -= STATUS_PENALTY.get(e["status"], 8)
        if e["source"] == "archive":
            base -= 5
        out.append((base, e))
    out.sort(key=lambda t: (-t[0], t[1]["slug"]))
    return out


def rrf_fuse(rankings: list[list[tuple[int, dict]]], k: int = 60) -> list[tuple[int, dict]]:
    """Reciprocal Rank Fusion（Cormack 2009）：score = Σ 1/(k+rank)，按名次融合多路排名。

    为什么按名次而非分数：AND 路分数含强意图权重（name +100）、OR 路含命中项数，
    两路刻度不可比——直接加权求和会让某一路的绝对分主导。RRF 只看名次，对
    「两路都排前」的条目给双份票，是稳健的融合口径（context-mode store.ts 同款 K=60）。
    返回同构 [(score, entry)]；分数放大 1e6 取整——放大倍数须保证相邻名次可分辨
    （×1000 时 1/61 与 1/62 舍入后同分，并列排序会吞掉真实名次差），次序与原始 RRF 分一致。
    """
    agg: dict[str, float] = {}
    by_key: dict[str, dict] = {}
    for ranking in rankings:
        for rank, (_s, e) in enumerate(ranking, start=1):
            key = str(e["path"])
            agg[key] = agg.get(key, 0.0) + 1.0 / (k + rank)
            by_key.setdefault(key, e)
    out = [(int(round(score * 1_000_000)), by_key[key]) for key, score in agg.items()]
    out.sort(key=lambda t: (-t[0], t[1]["slug"]))
    return out


def semantic_rank(query: str, entries: list[dict],
                  db_path: Path | None = None,
                  prune_keep: list | None = None) -> list[tuple[int, dict]] | None:
    """--semantic 向量检索路（v2.4.0 S6）：经 VectorCache（shadow index）取/建条目向量，
    与 query 向量做余弦，叠加时间衰减与状态/来源罚分后返回与 score_entry 同构的
    [(score, entry)] 列表（未命中过滤已完成，这里不做 AND 词面门槛）。

    prune_keep：prune 的"保留集"应传**全量条目路径**而非本函数的 entries——
    entries 可能已被时间窗过滤，用它做保留集会把窗口外条目的缓存误删，
    下次无窗口查询时被迫全量重嵌（缓存抖动，2026-10-07 OCR 评审修复）。
    未传时退回 entries（仅当调用方无过滤时等价）。

    embedding 后端不可用 → 返回 None（调用方回退词面打分并提示）。
    缓存残缺（个别条目重嵌失败）→ 该条不参与向量排序（shadow index 可残缺语义）。
    """
    try:
        from lib import similarity, vector_cache
        emb = similarity._get_embedder()
    except Exception:
        return None
    db_path = db_path if db_path is not None else VECTOR_CACHE_DB
    try:
        vc = vector_cache.VectorCache(db_path, emb.model_name)
        vecs = vc.sync(entries, emb.embed)
        vc.prune(prune_keep if prune_keep is not None else [e["path"] for e in entries])
        qv = emb.embed(query)
    except Exception as e:
        # shadow index 语义：缓存路任何失败都不得阻塞检索（回退词面），但失败原因
        # 必须可见——静默 except 会让"向量路从未生效"这种问题潜伏（2026-10-07 实测踩坑）
        print(f"⚠️ --semantic 向量路失败（{type(e).__name__}: {e}），回退词面打分",
              file=sys.stderr)
        return None
    out = []
    for e in entries:
        vec = vecs.get(str(e["path"]))
        if not vec:
            continue
        base = int(round(vector_cache.cosine(qv, vec) * 100))
        base += time_bonus(e)
        base -= STATUS_PENALTY.get(e["status"], 8)
        if e["source"] == "archive":
            base -= 5
        out.append((base, e))
    return out


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
                # v2.3.0：superseded 条目把失效理由带进提案，人工复核不再盲选
                why = "检索命中"
                if h["status"] == "superseded" and h.get("superseded_reason"):
                    why = f"检索命中；失效原因: {h['superseded_reason']}"
                f.write(f"| {h['slug']} | {h['status']} | {h['source']} | {why} |\n")
        return len(new)
    except OSError:
        return 0


def revive(slug: str, detail_dir: Path | None = None, archive_dir: Path | None = None,
           db: Path | None = None) -> tuple[bool, str]:
    """人工确认后的复活执行：dormant/superseded → active（原地）；
    archived → 移回 detail 再 active。写 review_log 留痕。
    （路径默认 None=调用时取模块属性，理由同 scan_memory_dirs。）"""
    detail_dir = detail_dir if detail_dir is not None else DETAIL_DIR
    archive_dir = archive_dir if archive_dir is not None else ARCHIVE_DIR
    db = db if db is not None else DB
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
    # v2.3.0 证据链：复活清空蒸馏状态位——源复活 → 既有蒸馏结论待重蒸馏（可逆闭环）
    fm2, body2 = parse_frontmatter(new_content)
    if str(fm2.get("distilled_at", "")).strip():
        fm2["distilled_at"] = ""
        new_content = dump_frontmatter(fm2, body2)
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
    ap.add_argument("--since", metavar="YYYY-MM-DD", default=None,
                    help="只返回发生时间 ≥ 此日的条目（valid_time→transaction_time→first_seen）")
    ap.add_argument("--until", metavar="YYYY-MM-DD", default=None,
                    help="只返回发生时间 ≤ 此日的条目")
    ap.add_argument("--window", type=int, metavar="DAYS", default=None,
                    help="只返回发生时间近 N 天的条目（等价 --since 今天-N天）")
    ap.add_argument("--semantic", action="store_true",
                    help="向量检索模式：余弦排序（不卡词面 AND），需 MEMORY_ECOLOGY_SIM_BACKEND=embedding"
                         " 且本地模型就绪；不可用自动回退词面打分。向量缓存为可重建 shadow index"
                         "（memories/.vector_cache.db，坏=无损）")
    ap.add_argument("--recall", action="store_true",
                    help="OR 召回兜底：多词查询在 AND 全命中之外，额外召回命中任一 term 的条目"
                         "并按 RRF 融合排序（v2.4.0 S7；默认关闭=行为锁定）")
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
    # 时间窗解析与过滤（v2.3.0）：非法日期直接报参数错误；无时态条目在启用过滤时跳过
    time_from = parse_date(args.since or "")
    time_to = parse_date(args.until or "")
    if args.since and time_from is None:
        ap.error(f"--since 日期无法解析: {args.since}")
        return 1
    if args.until and time_to is None:
        ap.error(f"--until 日期无法解析: {args.until}")
        return 1
    if args.window is not None:
        if args.window < 1:
            ap.error("--window 需为正整数（天）")
            return 1
        w_from = TODAY - datetime.timedelta(days=args.window)
        time_from = w_from if time_from is None else max(time_from, w_from)
    all_entries = scan_memory_dirs()
    undated_skipped = 0
    if time_from is not None or time_to is not None:
        kept = []
        for e in all_entries:
            d = event_date(e)
            if d is None:
                undated_skipped += 1
                continue
            if time_from is not None and d < time_from:
                continue
            if time_to is not None and d > time_to:
                continue
            kept.append(e)
        entries = kept
    else:
        entries = all_entries

    mode = "lexical"
    if args.semantic:
        from lib import similarity
        if similarity.effective_backend() != "embedding":
            print("⚠️ --semantic 需要 embedding 后端（MEMORY_ECOLOGY_SIM_BACKEND=embedding"
                  " + 本地模型，见 models/README.md），本轮回退词面打分")
        else:
            sem = semantic_rank(args.keyword, entries,
                                prune_keep=[e["path"] for e in all_entries])
            if sem is None:
                print("⚠️ --semantic 向量路初始化失败，本轮回退词面打分")
            else:
                scored = sem
                mode = "semantic"
    if mode != "semantic":
        scored = []
        for e in entries:
            s = score_entry(terms, e)
            if s is not None:
                scored.append((s, e))
        if args.recall and len([t for t in terms if t]) >= 2:
            # v2.4.0 S7：多词 AND 在词序/搭配不同时会全灭（实测「记忆 检索」AND=0 而
            # OR=5）。OR 召回兜底补这部分漏网，再与原 AND 结果做 RRF 融合。
            # 融合而非替换：AND 命中（强意图）保持在前，OR 独有（弱信号）排其后。
            or_hits = or_recall(terms, entries)
            if or_hits:
                # 传排名列表的列表：[AND 路, OR 路]（rrf_fuse 只吃名次，不吃分值）
                scored = rrf_fuse([scored, or_hits])
                mode = "lexical+recall"
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
            "updated": updated, "revive_proposed": proposed, "mode": mode,
            "time_filter": {"since": time_from.isoformat() if time_from else "",
                            "until": time_to.isoformat() if time_to else "",
                            "undated_skipped": undated_skipped},
            "hits": [{"id": h["slug"], "score": h["score"], "status": h["status"],
                      "type": h["type"], "source": h["source"],
                      "last_hit": h["last_hit"] or "",
                      "valid_time": h.get("valid_time") or "",
                      "transaction_time": h.get("transaction_time") or "",
                      "superseded_by": h.get("superseded_by") or "",
                      "superseded_reason": h.get("superseded_reason") or "",
                      "superseded_at": h.get("superseded_at") or "",
                      "evidence": h.get("evidence") or "",
                      "distilled_at": h.get("distilled_at") or "",
                      "preview": h["body"][:60]} for h in hits],
        }, ensure_ascii=False, indent=2))
        return 0 if hits else 1

    undated_note = f"；另跳过 {undated_skipped} 条无时态条目" if undated_skipped else ""
    mode_note = {"lexical+recall": "（AND+OR 召回 RRF 融合：含部分命中）",
                 "semantic": "（向量检索：余弦排序）"}.get(mode, "")
    scanned_note = "时间过滤后扫描" if (time_from is not None or time_to is not None) else "扫描"
    if not hits:
        print(f"未命中：{args.keyword}（{scanned_note} {len(entries)} 条{undated_note}）")
        return 1
    print(f"命中 {len(hits)}/{len(entries)} 条{undated_note}{mode_note}（状态越靠后越降权；归档区可查但靠后）:")
    for h in hits:
        print(f"  [{h['score']:>3}] {h['slug']}  [{h['status']}/{h['type']}] ({h['source']})")
        if h["status"] == "superseded":
            # v2.3.0：失效记忆不是消失，而是带理由降权（deja-vu「rejected 随命中返回」）
            why = h.get("superseded_reason") or "（未记录理由）"
            by = f"，被 {h['superseded_by']} 取代" if h.get("superseded_by") else ""
            when = f"（{h['superseded_at']}）" if h.get("superseded_at") else ""
            print(f"        ⚠️ 已失效{when}{by}：{why}")
        print(f"        {h['body'][:80]}")
    if proposed:
        print(f"  💡 {proposed} 条休眠/归档条目被命中 → 已写入复活候选（gate_log/revive-{TODAY.isoformat()}.md），"
              f"人工确认后执行 --revive <slug>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
