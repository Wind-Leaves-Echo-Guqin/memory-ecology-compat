#!/usr/bin/env python3
"""生态写入整合门 write_gate（版本迭代策略 v2 门①）。

把会话提取管道产出的 pending 候选整合进 L2 详情层（memories/detail/）：
1) 类型分型 episodic/semantic/procedural/lesson（LLM 初判 + 规则兜底）
2) 与已有 detail 条目相似比对（difflib，规范化后 ratio）
3) 四动作决策 ADD/UPDATE/NOOP/CONFLICT（相似命中才批量调 LLM；无命中规则直判 ADD）
4) 事件钟字段 valid_time/transaction_time（Graphiti 双时态轻量版）
5) 矛盾旧条目标 superseded 后移入 quarantine（永不物理删除）

设计：独立 gate 脚本（保护区外，不改提取管道）；只读 pending、只写
detail/quarantine/gate_log/eco.db；幂等（fingerprint 防重 + 每动作即时 commit +
并发锁）；原子写（临时文件+os.replace）；--dry-run 只报告不动手。

用法: python write_gate.py [--dry-run] [--pending DIR] [--detail DIR]
      [--db PATH] [--logdir DIR]   （cron 每日 13:05 no_agent）
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from lib.config import hermes_root
from lib.fs import atomic_write, norm, slug_of
from lib import safeio
from lib import llm as _llm
from lib import similarity
from lib.gatekit import acquire_lock_auto, release_lock, connect_db
from lib.memstore import (parse_frontmatter, dump_frontmatter, parse_candidates,
                          mark_consumed, load_detail, is_pending_active)  # PORT_SPEC §C：口径/解析单源

HERMES = hermes_root()
PENDING_DIR = HERMES / "memories" / "pending"
DETAIL_DIR = HERMES / "memories" / "detail"
QUARANTINE_DIR = HERMES / "memories" / "quarantine"
LOG_DIR = HERMES / "memories" / "gate_log"
DB = HERMES / "eco.db"
LOCK = HERMES / "scripts" / ".write_gate.lock"

SIM_NEAR = 0.80      # 近似重复（UPDATE/NOOP 候选）
SIM_SUSPECT = 0.50   # 疑似冲突窗口下界
MAX_BODY = 200       # detail 正文上限
MAX_OUTPUT = 8       # 单次最多处理候选条数
ACTIONS = ("ADD", "UPDATE", "NOOP", "CONFLICT")

PROMPT = """你是记忆整合器。以下是提取出的候选记忆（可能附带相似已有条目）。
对每条候选给出 type 和 action：
type: semantic(长期事实/偏好/属性) | episodic(一次性事件) | procedural(流程/做法) | lesson(教训)
action: ADD(新增) | UPDATE(与已有条目近似重复,应合并计数) | NOOP(无记忆价值) | CONFLICT(与已有条目同主题但事实相反/矛盾,旧条目应失效)
规则: 一次性临时内容即使新增也标 episodic; 近似重复优先 UPDATE; 同主题相反事实必须 CONFLICT; 琐碎无价值 NOOP。
输出 JSON 数组, 每项 {"idx": 数字, "type": "...", "action": "...", "target": "相似条目slug或空串", "note": "一句话理由"}。只输出 JSON。

候选与相似条目:
{context}
"""




def llm_decision(context: str) -> list[dict]:
    content = _llm.complete(PROMPT.replace("{context}", context[:4000]),
                            max_tokens=800, temperature=0.1)
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        return []
    items = json.loads(m.group(0))
    return [i for i in items if isinstance(i, dict) and "idx" in i]


def rule_type(text: str) -> str:
    """LLM 不可用时的规则兜底：类型初判。"""
    if re.search(r"流程|步骤|先.{0,6}再|做法是|习惯是|方法[:：]", text):
        return "procedural"
    if re.search(r"教训|以后.*要|别再|不要.*了|犯了|踩坑", text):
        return "lesson"
    if re.search(r"(今天|昨天|上周|\d{4}-\d{2}-\d{2}|已完成|已提交|已发|办完|做完)", text):
        return "episodic"
    return "semantic"




def find_similar(text: str, detail: list[dict], threshold: float) -> list[dict]:
    """返回相似度 ≥ threshold 的 detail 条目（按相似度降序）。
    批 5：相似度内核走 lib/similarity 统一层（默认 difflib 行为锁定；
    MEMORY_ECOLOGY_SIM_BACKEND=ngram/embedding 可切换——切换前先跑 eco_sim_eval）。"""
    hits = []
    for d in detail:
        ratio = similarity.ratio(text, d["body"])
        if ratio >= threshold:
            hits.append({"detail": d, "ratio": ratio})
    hits.sort(key=lambda h: h["ratio"], reverse=True)
    return hits




def add_entry(detail_dir: Path, text: str, fm_extra: dict) -> Path:
    now = datetime.date.today().isoformat()
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    fm = {
        "type": fm_extra.get("type", "semantic"),
        "status": "active",
        "occurrences": "1",
        "session_count": "1",
        "first_seen": now,
        "last_seen": now,
        "last_hit": now,  # PORT_SPEC §6 last_hit 最小件：创建即首 hit（后续由 inject 遥测更新）
        "valid_time": fm_extra.get("valid_time") or now,
        "transaction_time": ts,
        "last_verified": now,
        "origin_session_id": fm_extra.get("origin_session_id", ""),
        "superseded_by": "",
    }
    path = safeio.safe_entry_path(detail_dir, slug_of(text))
    if path.exists():
        # 已存在（如 pending 重建致 fingerprint 变化）：不覆盖，走 UPDATE 语义。
        # P1：读取/写入失败直接抛给上层记失败（候选不消费、下轮重试）——
        # 旧版 except OSError: pass 后直接 return，账本记了从未发生的 ADD（假成功）。
        old_fm, old_body = parse_frontmatter(path.read_text(encoding="utf-8"))
        try:
            old_fm["occurrences"] = str(int(old_fm.get("occurrences", "1")) + 1)
        except ValueError:
            old_fm["occurrences"] = "2"
        old_fm["last_seen"] = now
        old_fm["last_verified"] = now
        safeio.write_entry(path, dump_frontmatter(old_fm, old_body), kind="detail")
    else:
        safeio.write_entry(path, dump_frontmatter(fm, text[:MAX_BODY]), kind="detail")
    return path


def update_entry(d: dict) -> None:
    """近似重复：occurrences+1；跨天出现则 session_count+1；刷新 last_verified（防活跃记忆被误归档）。
    P1：同轮多条候选命中同一目标时，逐次从磁盘重读最新值再增补——入口快照会把
    两次 +1 都写在同一基线上，实际只涨 1（丢失更新）。"""
    today = datetime.date.today().isoformat()
    try:
        fm, body = parse_frontmatter(d["path"].read_text(encoding="utf-8"))
    except OSError:
        fm, body = dict(d["fm"]), d["body"]
    try:
        fm["occurrences"] = str(int(fm.get("occurrences", "1")) + 1)
    except ValueError:
        fm["occurrences"] = "2"
    if str(fm.get("last_seen", ""))[:10] != today:
        try:
            fm["session_count"] = str(int(fm.get("session_count", "1")) + 1)
        except ValueError:
            fm["session_count"] = "2"
    fm["last_seen"] = today
    fm["last_verified"] = today  # P1-1：活跃记忆持续复核，不被 eco_review 误归档
    safeio.write_entry(d["path"], dump_frontmatter(fm, body), kind="detail")


def supersede_entry(d: dict, new_slug: str, quarantine_dir: Path) -> Path:
    """矛盾旧条目：标 superseded 后移入 quarantine（保留可回滚）。"""
    fm = dict(d["fm"])
    fm["status"] = "superseded"
    fm["superseded_by"] = new_slug
    date_dir = quarantine_dir / datetime.date.today().isoformat()
    date_dir.mkdir(parents=True, exist_ok=True)
    target = date_dir / d["path"].name
    if target.exists():
        target = date_dir / f"{d['path'].stem}-{hashlib.md5(str(target).encode()).hexdigest()[:6]}.md"
    safeio.write_entry(d["path"], dump_frontmatter(fm, d["body"]), kind="detail")
    os.replace(d["path"], target)
    try:
        if d["path"].exists():
            d["path"].unlink()
    except OSError as e:
        print(f"⚠️ 原文件删除失败（quarantine 副本已存在）: {d['path'].name}: {e}")
    return target


def _fingerprint_done(conn: sqlite3.Connection, key: str) -> bool:
    fp = hashlib.md5(key.encode("utf-8")).hexdigest()
    try:
        return conn.execute("SELECT 1 FROM gate_log WHERE fingerprint=?", (fp,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False  # dry-run 无表时视为未处理


def log_gate(conn: sqlite3.Connection, fingerprint: str, action: str, target: str, note: str, dry: bool = False) -> None:
    if dry:
        return
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR IGNORE INTO gate_log(ts, fingerprint, action, target, note) VALUES(?,?,?,?,?)",
        (ts, fingerprint, action, target, note),
    )


def _match_target(decision: dict, sims: list) -> dict | None:
    """优先采用 LLM 指定的 target slug（P1-2），匹配不到才回退最相似条目。"""
    t = decision.get("target") or ""
    if t:
        for s in sims:
            if s["detail"]["name"] == t:
                return s["detail"]
    return sims[0]["detail"] if sims else None


def consumable_files(by_file: dict[str, list[dict]], terminal: set[str],
                     pending_dir: Path) -> set[str]:
    """P0-1：可消费文件判定。

    一个文件可消费 ⇔ 它解析出的**全部**候选都已终态（历史指纹已存在，或本轮成功处理）；
    无候选的文件照旧消费（与原行为一致）。被 MAX_OUTPUT 截断或处理失败的候选
    所在文件留在 pending，下轮续处理——不再被尾部统一改名静默吞掉。
    """
    out: set[str] = set()
    for fname, cs in by_file.items():
        if all(f"{c['file']}|{c['raw']}" in terminal for c in cs):
            out.add(fname)
    for p in pending_dir.glob("*.md"):
        if is_pending_active(p.name) and p.name not in by_file:
            out.add(p.name)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pending", type=Path, default=PENDING_DIR)
    ap.add_argument("--detail", type=Path, default=DETAIL_DIR)
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--logdir", type=Path, default=LOG_DIR)
    args = ap.parse_args(argv)

    locked, how = acquire_lock_auto(LOCK)
    if not locked:
        print("⚠️ 已有 write_gate 实例在运行，本轮跳过")
        return 0
    if how == "stale-cleared":
        print("🧹 清理了失效残留锁（持有进程已不存在），本轮正常执行")
    try:
        return _run(args)
    finally:
        release_lock(LOCK)


def _run(args) -> int:
    if not args.dry_run:
        args.detail.mkdir(parents=True, exist_ok=True)
        args.logdir.mkdir(parents=True, exist_ok=True)

    # dry-run 且库不存在：connect_db 用 :memory: 顶替——不建库不留 0 字节文件；
    # 库已存在时照常连接读真指纹（dry-run 的去重报告行为与修复前完全一致）
    conn = connect_db(args.db, dry_run=args.dry_run)
    if not args.dry_run:
        conn.execute("""CREATE TABLE IF NOT EXISTS gate_log(
            ts TEXT, fingerprint TEXT PRIMARY KEY, action TEXT, target TEXT, note TEXT)""")

    cands_all = parse_candidates(args.pending)
    # 按文件分组（P0-1 消费判定用）；候选键 = "file|raw"（与指纹口径一致）
    by_file: dict[str, list[dict]] = {}
    for c in cands_all:
        by_file.setdefault(c["file"], []).append(c)
    terminal: set[str] = set()  # 终态 = 历史指纹已存在 或 本轮成功处理
    fresh = []
    for c in cands_all:
        key = f"{c['file']}|{c['raw']}"
        if _fingerprint_done(conn, key):
            terminal.add(key)
        else:
            fresh.append(c)
    # P1-9：先过滤已处理 fingerprint 再取前 MAX_OUTPUT（防候选饥饿）；
    # P0-1：被截断的候选其所在文件本轮不消费，留待下轮
    cands = fresh[:MAX_OUTPUT]
    detail = load_detail(args.detail)
    today = datetime.date.today().isoformat()
    report = []
    failures = 0

    for c in cands:
        key = f"{c['file']}|{c['raw']}"
        fp = hashlib.md5(key.encode("utf-8")).hexdigest()
        text = c["text"]
        if not norm(text):
            report.append(f"SKIP     {text[:40]}（空/纯标点，无信息量）")
            log_gate(conn, fp, "NOOP", "", "空内容", args.dry_run)
            terminal.add(key)
            continue
        sims = find_similar(text, detail, SIM_SUSPECT)
        decision = None
        if sims:
            # 有相似命中：调 LLM 决策（上下文只带当前候选 + 相似条目，省 token）
            ctx_lines = [f"候选 {c['idx']}: [{c['ctype']}] {text}"]
            ctx_lines.append("相似条目:")
            for i, s in enumerate(sims[:3]):
                ctx_lines.append(f"  {i}: {s['detail']['name']} (ratio {s['ratio']:.2f}) {s['detail']['body'][:80]}")
            try:
                decisions = llm_decision("\n".join(ctx_lines))
                for dd in decisions:
                    if dd.get("idx") == c["idx"]:
                        decision = dd
                        break
            except Exception as e:
                print(f"⚠️ LLM 决策失败: {e}（回退规则判定）")
        # 规则兜底
        if decision is None:
            ctype = rule_type(text)
            if sims and sims[0]["ratio"] >= SIM_NEAR:
                action = "UPDATE" if sims[0]["ratio"] < 0.95 else "NOOP"
            else:
                action = "ADD"
            decision = {"type": ctype, "action": action, "target": sims[0]["detail"]["name"] if sims else "", "note": "规则兜底"}

        action = decision.get("action", "ADD")
        if action not in ACTIONS:
            # LLM 输出了非法动作：回退规则判定（不写 NOOP 指纹，不静默丢弃）
            ctype = rule_type(text)
            if sims and sims[0]["ratio"] >= SIM_NEAR:
                action = "NOOP" if sims[0]["ratio"] >= 0.95 else "UPDATE"
            else:
                action = "ADD"
            decision = {"type": ctype, "action": action,
                        "target": sims[0]["detail"]["name"] if sims else "", "note": "非法action回退规则"}
        # LLM 在线时也可能对完全同文（≥0.95）判 UPDATE——与规则兜底同口径钳制为 NOOP，
        # 防 occurrences 被同一文本反复膨胀（P1）
        if decision.get("action") == "UPDATE" and sims and sims[0]["ratio"] >= 0.95:
            decision = dict(decision, action="NOOP",
                            note=decision.get("note", "") + "（≥0.95 同文钳制为 NOOP）")
        ctype = decision.get("type", rule_type(text))
        note = decision.get("note", "")
        target = ""
        try:
            if action == "ADD":
                if not args.dry_run:
                    p = add_entry(args.detail, text, {"type": ctype})
                    target = p.stem
                else:
                    target = f"(new:{slug_of(text)})"
                report.append(f"ADD      {text[:40]} → {target} [type={ctype}] {note}")
                log_gate(conn, fp, "ADD", target, note, args.dry_run)
            elif action == "UPDATE":
                hit = _match_target(decision, sims)
                if hit:
                    target = hit["name"]
                    if not args.dry_run:
                        update_entry(hit)
                    report.append(f"UPDATE   {text[:40]} → {target} (occurrences+1) {note}")
                    log_gate(conn, fp, "UPDATE", target, note, args.dry_run)
                else:
                    report.append(f"UPDATE   {text[:40]} → 无目标，按 ADD 处理")
                    if not args.dry_run:
                        p = add_entry(args.detail, text, {"type": ctype})
                        log_gate(conn, fp, "ADD", p.stem, note, args.dry_run)
            elif action == "CONFLICT":
                hit = _match_target(decision, sims)
                if hit:
                    target = hit["name"]
                    if not args.dry_run:
                        # R10（v2.2.0 review）：先失效旧条目再写新条目；P1 补强——
                        # 写新失败时把旧条目从 quarantine 原位还原（fm 快照是 supersede 前的），
                        # 候选下轮可重试，不再留下「旧条目已废 + 新条目未落盘」且随 .done 丢失的半程态
                        qtarget = supersede_entry(hit, slug_of(text), QUARANTINE_DIR)
                        try:
                            p = add_entry(args.detail, text, {"type": ctype})
                        except Exception:
                            try:
                                os.replace(qtarget, hit["path"])
                                safeio.write_entry(hit["path"], dump_frontmatter(hit["fm"], hit["body"]),
                                                   kind="detail")
                            except Exception as e2:
                                print(f"⚠️ 旧条目还原失败（quarantine 副本仍在，可人工恢复）: {e2}")
                            raise
                        report.append(f"CONFLICT {text[:40]} → 新条目 {p.stem}，旧条目 {target} 已 superseded→quarantine {note}")
                        log_gate(conn, fp, "CONFLICT", target, f"new={p.stem} {note}", args.dry_run)
                    else:
                        report.append(f"CONFLICT {text[:40]} → (dry-run) 旧条目 {target} 将失效")
                        log_gate(conn, fp, "CONFLICT", target, note, args.dry_run)
                else:
                    report.append(f"CONFLICT {text[:40]} → 无目标条目，按 ADD 处理")
                    if not args.dry_run:
                        p = add_entry(args.detail, text, {"type": ctype})
                        log_gate(conn, fp, "ADD", p.stem, note, args.dry_run)
            else:  # NOOP
                target = decision.get("target", "")
                report.append(f"NOOP     {text[:40]} {note}")
                log_gate(conn, fp, "NOOP", target, note, args.dry_run)
        except Exception as e:
            failures += 1
            report.append(f"⚠️ 处理失败 {text[:40]}: {e}")
            print(f"⚠️ {text[:40]}: {e}")
        else:
            terminal.add(key)  # P0-1：成功处理才终态；失败候选留待下轮重试
        if not args.dry_run:
            conn.commit()  # P1-4：每个动作后立即落库，崩溃窗口不破坏幂等

    # v0.3 对齐门③④：零新候选轮次写 idle 心跳（"门在跑"有据可查）；
    # fingerprint 按日哈希，gate_log 主键不跨日冲突
    if not args.dry_run and not fresh:
        log_gate(conn, hashlib.md5(f"idle|{today}".encode("utf-8")).hexdigest(),
                 "idle", "", "heartbeat 零新候选（门在跑）")
        conn.commit()

    conn.commit()
    conn.close()

    # Q22（2026-09-06）+ P0-1（2026-10-04）：只消费「全部候选已达终态」的文件——
    # MAX_OUTPUT 截断的、处理失败的历史上被尾部统一改名 .done.md 静默吞掉，现在留待下轮
    if not args.dry_run:
        consume = consumable_files(by_file, terminal, args.pending)
        deferred = sorted(set(by_file) - consume)
        if deferred:
            print(f"⏸ {len(deferred)} 个文件存在未终态候选，留待下轮: "
                  f"{', '.join(deferred[:5])}{'…' if len(deferred) > 5 else ''}")
        mark_consumed(args.pending, only=consume)

    if args.dry_run:
        print("== DRY-RUN（未修改任何文件）==")
    else:
        out = args.logdir / f"gate-{today}.md"
        lines = [f"# 写入整合门 {today}", ""] + [f"- {r}" for r in report]
        atomic_write(out, "\n".join(lines) + "\n")
        print(f"✅ 整合日志 → {out}")
    for r in report:
        print(r)
    skipped = len(cands_all) - len(fresh)
    print(f"本轮处理 {len(cands)} 条候选（另有 {skipped} 条历史指纹跳过），"
          f"报告 {len(report)} 条，失败 {failures} 条")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
