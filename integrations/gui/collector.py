#!/usr/bin/env python3
"""只读数据采集层（memory-ecology GUI · 观测舱）。

纪律（设计稿 H2/H3/H6）：
- 本模块对数据根零写入：文件只读、SQLite mode=ro、锁文件不碰（只看存在性/mtime）
- 路径解析：--root 参数 > MEMORY_ECOLOGY_ROOT > 生产根探测 > 仓库根兜底（零个人路径硬编码）
- 一切读取 utf-8 + errors=replace；退化态（缺目录/缺库）返回空值不抛异常
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── 根解析 ──────────────────────────────────────────────────────────────
_ARG_ROOT: str | None = None      # eco_gui.py 启动时注入
_ARG_SCRIPTS: str | None = None


def set_root(root: str | None, scripts: str | None = None) -> None:
    global _ARG_ROOT, _ARG_SCRIPTS
    if root:
        _ARG_ROOT = root
    if scripts:
        _ARG_SCRIPTS = scripts


def _looks_like_eco_root(p: Path) -> bool:
    return p.is_dir() and ((p / "memories").is_dir() or (p / "scripts" / "lib").is_dir())


def data_root() -> Path:
    if _ARG_ROOT:
        return Path(_ARG_ROOT)
    env = os.environ.get("MEMORY_ECOLOGY_ROOT")
    if env:
        return Path(env)
    home = Path.home()
    for cand in (home / "AppData" / "Local" / "hermes", home / ".hermes"):
        if _looks_like_eco_root(cand):
            return cand
    # 兜底：跟随安装位置（integrations/gui/ → 仓库根）
    return Path(__file__).resolve().parents[2]


def scripts_dir() -> Path | None:
    """检索 CLI 所在目录：显式参数 > ECO_SCRIPTS_DIR > 数据根 scripts/ > 发布树 src/memory_ecology/。"""
    if _ARG_SCRIPTS:
        p = Path(_ARG_SCRIPTS)
        return p if p.is_dir() else None
    env = os.environ.get("ECO_SCRIPTS_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    for cand in (data_root() / "scripts", Path(__file__).resolve().parents[2] / "src" / "memory_ecology"):
        if (cand / "eco_note_query.py").is_file():
            return cand
    return None


def agent_index() -> Path:
    return Path.home() / ".memory-ecology" / "designs"


# ── 基础工具 ────────────────────────────────────────────────────────────
def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_fm(text: str) -> tuple[dict, str]:
    """简化 frontmatter（--- 块）解析，返回 (dict, 正文)。"""
    if text.startswith("\ufeff"):
        text = text[1:]
    fm: dict = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            block = text[3:end].strip()
            body = text[end + 4:].strip()
            for line in block.splitlines():
                if ":" in line and not line.startswith((" ", "\t")):
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, body


# ── 缓存（短 TTL，配合巡检） ────────────────────────────────────────────
_CACHE: dict = {}


def _cached(key: str, ttl: float, fn):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    data = fn()
    _CACHE[key] = (now, data)
    return data


# ── L1 / 画像 ───────────────────────────────────────────────────────────
def _split_entries(text: str) -> list[dict]:
    """L1 文件按 § 分行拆条目，返回 [{"text": ...}]。"""
    parts, cur = [], []
    for line in text.splitlines():
        if line.strip() == "§":
            if cur:
                parts.append({"text": "\n".join(cur).strip()})
                cur = []
        elif line.strip():
            cur.append(line.strip())
    if cur:
        parts.append({"text": "\n".join(cur).strip()})
    return parts


def _metrics():
    """口径单源（PORT_SPEC §4-C）：把生产 scripts/ 挂上 sys.path 后取 lib.metrics——
    GUI 的 L1 计数与门②同源。探测失败返回 None（调用方回退近似口径，fail-open 不崩）。
    注意：生产 scripts/lib 同步 metrics.py 之前（deploy-live 落地前），本函数在干净
    进程中会走 None 分支——这是已知待办，不是 bug。"""
    sd = scripts_dir()
    if not sd:
        return None
    try:
        if str(sd) not in sys.path:
            sys.path.append(str(sd))  # 审查 m3：append 防止生产树遮蔽 GUI/stdlib 同名模块
        import importlib
        return importlib.import_module("lib.metrics")
    except Exception:
        return None


def _l1_file(name: str) -> dict:
    root = data_root()
    p = root / "memories" / name
    text = _read(p)
    entries = _split_entries(text)
    # L1↔L2 关联（J 边）：条目文本 ≈ detail 正文的行（promote 后双向驻留）
    links: dict[str, str] = {}
    for d in scan_l2_fresh():
        body = d["body"].strip()
        if body and len(body) >= 12:
            links[body[:60]] = d["slug"]
    for e in entries:
        e["linked_slug"] = next((s for head, s in links.items()
                                 if head[:15] and head[:15] in e["text"]), None)
    mtr = _metrics()
    if mtr is not None:
        try:  # 审查 n5：fail-open 契约守全——口径源存在但残缺时也回退，不打穿 scan 链
            chars = mtr.chars_of(mtr.parse_l1(text)[0])
            # 修复占位 bug：原对 MEMORY/USER 都硬编码 2550（USER 实际管理线 1275）
            quota = mtr.MEMORY_TRIGGER if name.startswith("MEMORY") else mtr.USER_TRIGGER
        except Exception:
            mtr = None
    if mtr is None:
        chars = len(text.replace("\n", "").replace(" ", ""))  # 兜底近似（口径源不可达时）
        quota = 2550 if name.startswith("MEMORY") else 1275  # 审查 m4：按名取线，不再一刀切
    return {"name": name, "chars": chars,
            "raw_chars": len(text), "quota": quota, "entries": entries,
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds") if p.exists() else None}


def scan_l1() -> dict:
    return {"memory": _l1_file("MEMORY.md"), "user": _l1_file("USER.md")}


# ── L2 详情层 ───────────────────────────────────────────────────────────
def scan_l2_fresh() -> list[dict]:
    d = data_root() / "memories" / "detail"
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        fm, body = parse_fm(_read(p))
        out.append({"slug": p.stem, "type": fm.get("type", "?"),
                    "status": fm.get("status", "?"),
                    "occ": _int(fm.get("occurrences")), "sess": _int(fm.get("session_count")),
                    "first": fm.get("first_seen", ""), "last": fm.get("last_seen", ""),
                    "verified": fm.get("last_verified", ""),
                    "valid_time": fm.get("valid_time", ""),
                    "transaction_time": fm.get("transaction_time", ""),
                    "origin": fm.get("origin_session_id", ""),
                    "superseded_by": fm.get("superseded_by", "") or None,
                    "chars": len(body), "body": body})
    return out


def scan_l2() -> list[dict]:
    return _cached("l2", 8, scan_l2_fresh)


def detail_body(slug: str) -> dict | None:
    p = data_root() / "memories" / "detail" / f"{slug}.md"
    if not p.is_file():
        return None
    fm, body = parse_fm(_read(p))
    return {"slug": slug, "fm": fm, "body": body}


def _int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


# ── 辅助区（候选/隔离/画像候选/门日志/备份） ─────────────────────────────
def aux_dirs() -> dict:
    mem = data_root() / "memories"
    out = {}
    for key, sub, done in (("pending", "pending", True), ("quarantine", "quarantine", False),
                           ("user_candidates", "user_candidates", False)):
        d = mem / sub
        files = []
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.suffix == ".md" and not (done and ".done" in p.name):
                    if p.name != "README.md":
                        files.append({"name": p.name, "mtime": _mtime(p),
                                      "preview": _read(p)[:400]})
        out[key] = files
    logs = []
    logdir = mem / "gate_log"
    if logdir.is_dir():
        for p in sorted(logdir.glob("*.md"), key=lambda x: x.name, reverse=True):
            kind = ("gate" if p.name.startswith("gate-") else
                    "review" if p.name.startswith("review-") else
                    "eval" if p.name.startswith("eval-") else "other")
            logs.append({"name": p.name, "kind": kind, "mtime": _mtime(p)})
    out["gate_logs"] = logs
    return out


def gate_log_day(date: str) -> dict | None:
    logdir = data_root() / "memories" / "gate_log"
    if not re.fullmatch(r"[\d-]{8,10}", date or ""):
        return None
    for pat in (f"gate-{date}.md", f"review-{date}.md", f"eval-{date}.md"):
        p = logdir / pat
        if p.is_file():
            return {"name": p.name, "body": _read(p)}
    return None


def backup_strata() -> list[dict]:
    """B8 备份链：memories/ 与 experiences/ 的 .bak-* 按日期聚合成"岩层"。"""
    root = data_root()
    buckets: dict[str, list] = {}
    for sub in ("memories", "experiences"):
        d = root / sub
        if not d.is_dir():
            continue
        for p in d.glob("*.bak*"):
            m = re.search(r"(\d{8})", p.name)
            day = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else "未知日期"
            buckets.setdefault(day, []).append(p.name)
    return [{"date": k, "n": len(v), "files": v}
            for k, v in sorted(buckets.items(), reverse=True)]


def _mtime(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""


# ── 经验笔记本 ──────────────────────────────────────────────────────────
_EXP_KEYS = ("title", "symptom", "cause", "action", "evidence", "boundary")


def _parse_exp_body(body: str) -> dict:
    secs: dict = {}
    cur = None
    for line in body.splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m and m.group(1) in _EXP_KEYS:
            cur = m.group(1)
            secs[cur] = m.group(2).strip()
        elif cur and line.strip():
            secs[cur] = (secs.get(cur, "") + " " + line.strip()).strip()
    return secs


def scan_exp_fresh() -> dict:
    d = data_root() / "experiences"
    items = []
    if d.is_dir():
        for p in sorted(d.glob("exp-*.md")):
            fm, body = parse_fm(_read(p))
            secs = _parse_exp_body(body)
            items.append({"id": fm.get("id", p.stem), "type": fm.get("type", "?"),
                          "status": fm.get("status", "?"),
                          "created": (fm.get("created", "") or "")[:10],
                          "last_hit": fm.get("last_hit", "") or "",
                          "provenance": fm.get("provenance", ""),
                          "distilled_to": fm.get("distilled_to", "") or None,
                          "title": secs.get("title", "(无标题)"),
                          "symptom": secs.get("symptom", ""),
                          "sections": {k: secs.get(k, "") for k in _EXP_KEYS if k != "title"}})
    items.sort(key=lambda x: x["created"], reverse=True)
    pend = []
    pd = d / "pending"
    if pd.is_dir():
        for p in sorted(pd.glob("*.md")):
            pend.append({"name": p.name, "mtime": _mtime(p), "preview": _read(p)[:300]})
    return {"items": items, "pending": pend}


def scan_experiences() -> dict:
    return _cached("exp", 8, scan_exp_fresh)


# ── eco.db（只读） ──────────────────────────────────────────────────────
def _ro_db(p: Path) -> sqlite3.Connection | None:
    if not p.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None


def eco_logs(limit: int = 200) -> dict:
    con = _ro_db(data_root() / "eco.db")
    out: dict = {"rows": [], "counts": {}}
    if not con:
        return out
    try:
        for t in ("gate_log", "quota_log", "distill_log", "review_log"):
            out["counts"][t] = con.execute(f"select count(*) from {t}").fetchone()[0]
        rows = []
        rows += [("gate", ts, act, tgt or "", note or "") for ts, act, tgt, note in
                 con.execute("select ts,action,target,note from gate_log")]
        rows += [("quota", ts, act, slug or "", f"{(prev or '')} → {(reason or '')}".strip(" →"))
                 for ts, act, slug, prev, reason in
                 con.execute("select ts,action,slug,l1_preview,reason from quota_log")]
        rows += [("distill", ts, act, tgt or "", note or "") for ts, act, tgt, note in
                 con.execute("select ts,action,target,note from distill_log")]
        rows += [("review", ts, act, slug or "", reason or "") for ts, act, slug, reason in
                 con.execute("select ts,action,slug,reason from review_log")]
        rows.sort(key=lambda r: r[1], reverse=True)
        out["rows"] = [{"table": t, "ts": ts, "action": act, "target": tgt, "note": note}
                       for t, ts, act, tgt, note in rows[:limit]]
        latest = {}
        for t, ts, act, tgt, note in rows:
            if t not in latest:
                latest[t] = {"ts": ts, "action": act}
        out["latest"] = latest
    finally:
        con.close()
    return out


# ── cron 调度 ───────────────────────────────────────────────────────────
_ECO_JOB_KW = ("生态", "记忆", "经验", "基因库", "体检")


def cron_info() -> dict:
    root = data_root()
    jobs = []
    p = root / "cron" / "jobs.json"
    jobs_raw: list = []
    if p.is_file():
        try:
            raw = json.loads(_read(p))
            jobs_raw = raw if isinstance(raw, list) else raw.get("jobs", [])
        except json.JSONDecodeError:
            jobs_raw = []
        for j in jobs_raw:
            name = str(j.get("name", ""))
            jobs.append({"id": j.get("id", ""), "name": name,
                         "eco": any(k in name for k in _ECO_JOB_KW),
                         "display": j.get("schedule_display") or (j.get("schedule") or {}).get("display", "?"),
                         "enabled": j.get("enabled", True),
                         "last_status": j.get("last_status"),
                         "last_run_at": (j.get("last_run_at") or "")[:16].replace("T", " "),
                         "streak": j.get("failure_streak") or 0,
                         "script": j.get("script") or "",
                         "provider": j.get("provider_snapshot") or j.get("provider"),
                         "model": j.get("model_snapshot") or j.get("model")})
    # 近 7 天失败（告警条口径，executions.db 只读）+ 失败详情（供复制全文）
    fails: dict[str, int] = {}
    fail_details: list[dict] = []
    n_exec_7d = 0
    con = _ro_db(root / "cron" / "executions.db")
    if con:
        try:
            since = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
            n_exec_7d = con.execute(
                "select count(*) from executions where finished_at >= ?", (since,)).fetchone()[0]
            for job_id, n in con.execute(
                    "select job_id, count(*) from executions where status='failed' "
                    "and finished_at >= ? group by job_id", (since,)):
                fails[job_id] = n
            for job_id, fin, err in con.execute(
                    "select job_id, finished_at, error from executions where status='failed' "
                    "and finished_at >= ? order by finished_at desc limit 20", (since,)):
                fail_details.append({"job": job_id, "job_name": "", "finished_at": (fin or "")[:19],
                                     "error": (err or "")[:800]})
        except sqlite3.Error:
            pass
        finally:
            con.close()
    name_by_id = {j.get("id"): j.get("name", j.get("id", "?")) for j in jobs_raw}
    for fd in fail_details:
        fd["job_name"] = name_by_id.get(fd["job"], fd["job"])
        # v0.3.1：供 GUI 一键修复（重跑/固定模型）——取任务当前 provider/model 快照
        j = next((x for x in jobs_raw if x.get("id") == fd["job"]), {})
        fd["provider"] = j.get("provider_snapshot") or j.get("provider") or ""
        fd["model"] = j.get("model_snapshot") or j.get("model") or ""
        fd["schedule"] = j.get("schedule_display") or ""
    now_iso = datetime.now().isoformat(timespec="seconds")
    return {"jobs": jobs, "total": len(jobs),
            "eco_count": sum(1 for j in jobs if j["eco"]),
            "recent_fails": [{"job": name_by_id.get(k, k), "n": v} for k, v in fails.items()],
            # v0.3（T2）双口径字段：口径标注到采集时点，前端分列展示不混称
            "executions_7d": {"n_jobs": len(fails), "n_fails": sum(fails.values()),
                              "n_exec": n_exec_7d, "ts": now_iso},
            "jobs_snapshot": {"ok": sum(1 for j in jobs if j.get("last_status") == "ok"),
                              "err": sum(1 for j in jobs if j.get("last_status") not in ("ok", None)),
                              "ts": now_iso},
            "fail_details": fail_details}


# ── 技能库 ──────────────────────────────────────────────────────────────
def _fm_raw(text: str) -> str:
    m = re.match(r"^\ufeff?---\n(.*?)\n---", text, re.S)
    return m.group(1) if m else ""


def scan_skills_fresh() -> dict:
    root = data_root() / "skills"
    skills: list[dict] = []
    archive: list[str] = []
    candidates: list[str] = []
    if root.is_dir():
        for p in sorted(root.rglob("SKILL.md")):
            rel = p.parent.relative_to(root).as_posix()
            if rel.startswith(".archive"):
                archive.append(rel.split("/", 1)[-1] if "/" in rel else p.parent.name)
                continue
            if rel.startswith(".candidates"):
                candidates.append(p.parent.name)
                continue
            text = _read(p)
            fmraw = _fm_raw(text)
            fm, _ = parse_fm(text)
            relm = re.search(r"related_skills:\s*\[([^\]]*)\]", fmraw)
            related = [x.strip().strip("'\"") for x in relm.group(1).split(",") if x.strip()] if relm else []
            if not related:
                m1 = re.search(r"related_skills:\s*([\w-]+)", fmraw)
                if m1:
                    related = [m1.group(1)]
            desc = fm.get("description", "")
            skills.append({"name": p.parent.name, "cat": rel.split("/")[0] if "/" in rel else "(顶层)",
                           "path": rel, "desc": desc[:120], "version": fm.get("version", "?"),
                           "status": fm.get("status", "undeclared"), "fate": fm.get("fate", "undeclared"),
                           "domain": fm.get("domain", ""), "environment": fm.get("environment", ""),
                           "evolved_from": fm.get("evolved_from", "") or None,
                           "merged_into": fm.get("merged_into", "") or None,
                           "related": related, "declared": fm.get("status") is not None,
                           "three": all(fm.get(k) for k in ("name", "description", "version")),
                           "chars": len(text), "hidden": fm.get("hidden") == "true"})
    names = {s["name"] for s in skills} | {s for s in archive} | set(candidates)
    for s in skills:
        s["related"] = [r for r in s["related"] if r in names]
        s["dangling"] = [r for r in s["related"] if r not in {x["name"] for x in skills}]
    indeg: dict[str, int] = {}
    for s in skills:
        for r in s["related"]:
            if r in {x["name"] for x in skills}:
                indeg[r] = indeg.get(r, 0) + 1
    for s in skills:
        s["indeg"] = indeg.get(s["name"], 0)
    # 同名副本 = 确定冗余
    seen: dict[str, list[str]] = {}
    for s in skills:
        seen.setdefault(s["name"], []).append(s["path"])
    for s in skills:
        s["dup"] = len(seen[s["name"]]) > 1
    cats = {}
    for s in skills:
        cats.setdefault(s["cat"], []).append(s["name"])
    return {"skills": skills, "cats": cats, "archive": archive, "candidates": candidates,
            "unique": len(seen), "declared": sum(1 for s in skills if s["declared"]),
            "three_ok": sum(1 for s in skills if s["three"])}


def scan_skills() -> dict:
    return _cached("skills", 20, scan_skills_fresh)


def skill_md(rel_path: str) -> dict | None:
    root = data_root() / "skills"
    if ".." in rel_path or rel_path.startswith(("/", "\\")):
        return None
    p = root / rel_path / "SKILL.md"
    if not p.is_file():
        return None
    return {"path": rel_path, "body": _read(p)}


# ── 版本 / 体检 / 基因库 ────────────────────────────────────────────────
def version_info() -> dict:
    root = data_root()
    text = _read(root / "VERSION.md")
    m = re.search(r"生态版本[::]\s*(v[\d.]+)", text)
    rows = []
    for line in text.splitlines():
        if line.startswith("| 20"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 5:
                rows.append({"date": cells[0], "gene": cells[1], "cron": cells[2],
                             "fail": cells[3], "mem": cells[4]})
    return {"version": m.group(1) if m else "?", "health_rows": rows[:14]}


def score_history() -> list:
    p = agent_index() / "eco_score_history.json"
    if not p.is_file():
        return []
    try:
        return json.loads(_read(p))
    except json.JSONDecodeError:
        return []


def health_report() -> dict:
    cands = sorted(agent_index().glob("生态体检报告-v*.md"))
    cands = [c for c in cands if ".bak" not in c.name]
    if not cands:
        cands = sorted((Path.home() / "Desktop").glob("生态体检报告-v*.md"))
        cands = [c for c in cands if ".bak" not in c.name]
    if not cands:
        return {"body": None, "name": None}
    p = cands[-1]
    return {"body": _read(p), "name": p.name, "mtime": _mtime(p)}


def report_archives() -> list[dict]:
    """v0.3（T10）：体检报告按日归档列表（新→旧）+ 当前覆盖式文件。

    返回 [{name, day, mtime, source}]，source=archive/current，day=YYYY-MM-DD。
    """
    out = []
    d = agent_index() / "体检报告存档"
    if d.is_dir():
        for p in sorted(d.glob("*.md"), reverse=True):
            out.append({"name": p.name, "day": p.stem, "mtime": _mtime(p), "source": "archive"})
    cur = health_report()
    if cur.get("name"):
        out.append({"name": cur["name"], "day": (cur.get("mtime") or "")[:10] or cur["name"],
                    "mtime": cur.get("mtime") or "", "source": "current"})
    return out


def report_by_name(name: str) -> dict | None:
    """v0.3：按文件名读归档/当前报告（对比视图用）。仅允许纯文件名，防路径穿越。"""
    if not name or "/" in name or "\\" in name or ".." in name or not name.endswith(".md"):
        return None
    for p in (agent_index() / "体检报告存档", agent_index(), Path.home() / "Desktop"):
        c = p / name
        if c.is_file():
            return {"name": name, "body": _read(c), "mtime": _mtime(c)}
    return None


def eval_reports() -> list[dict]:
    logdir = data_root() / "memories" / "gate_log"
    out = []
    if logdir.is_dir():
        for p in sorted(logdir.glob("eval-*.md"), reverse=True):
            out.append({"name": p.name, "body": _read(p)})
    return out


def gene_log() -> list[str]:
    root = data_root() / "ecosystem.git"
    if not (root / ".git").is_dir() and not root.name.endswith(".git"):
        return []
    try:
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        r = subprocess.run(["git", "-C", str(root), "log", "--oneline", "-10"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=8, creationflags=flags)
        return [l for l in (r.stdout or "").splitlines() if l.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return []


# ── 状态栏 / 锁 / 总览 ──────────────────────────────────────────────────
def lock_info() -> dict:
    mem = data_root() / "memories"
    locks = {}
    for name in ("MEMORY.md.lock", "USER.md.lock"):
        p = mem / name
        if p.is_file():
            mt = _mtime(p)
            age_h = (time.time() - p.stat().st_mtime) / 3600 if p.exists() else None
            locks[name] = {"mtime": mt, "age_hours": round(age_h, 1) if age_h is not None else None}
    return locks


def scan_candidates() -> dict:
    """v0.3（T8）三源候选聚合：技能 .candidates + 画像 user_candidates + 经验 pending。"""
    root = data_root()
    skill_cands = []
    cd = root / "skills" / ".candidates"
    if cd.is_dir():
        for p in sorted(cd.glob("*/SKILL.md")):
            fm, body = parse_fm(_read(p))
            skill_cands.append({"name": p.parent.name, "kind": "skill",
                                "desc": (fm.get("description", "") or body[:80])[:120],
                                "mtime": _mtime(p), "path": p.parent.relative_to(root).as_posix()})
        for p in sorted(cd.glob("*.md")):
            if p.name != "README.md":
                fm, body = parse_fm(_read(p))
                skill_cands.append({"name": p.stem, "kind": "skill",
                                    "desc": (fm.get("description", "") or body[:80])[:120],
                                    "mtime": _mtime(p), "path": p.relative_to(root).as_posix()})
    user_cands = [{"name": f["name"], "kind": "profile", "desc": f["preview"][:120],
                   "mtime": f["mtime"], "path": f"memories/user_candidates/{f['name']}"}
                  for f in aux_dirs()["user_candidates"]]
    exp_cands = [{"name": f["name"], "kind": "experience", "desc": f["preview"][:120],
                  "mtime": f["mtime"], "path": f"experiences/pending/{f['name']}"}
                 for f in scan_experiences()["pending"]]
    return {"items": skill_cands + user_cands + exp_cands,
            "by_kind": {"skill": len(skill_cands), "profile": len(user_cands),
                        "experience": len(exp_cands)}}


def candidate_detail(kind: str, name: str) -> dict | None:
    """v0.3.1：单个候选的全文详情（孵化台决策用）。"""
    root = data_root()
    paths = {
        "skill": [root / "skills" / ".candidates" / name / "SKILL.md",
                  root / "skills" / ".candidates" / f"{name}.md"],
        "profile": [root / "memories" / "user_candidates" / name],
        "experience": [root / "experiences" / "pending" / name],
    }
    for p in paths.get(kind, []):
        if p.is_file():
            fm, body = parse_fm(_read(p))
            return {"kind": kind, "name": name, "path": str(p),
                    "frontmatter": fm, "body": body, "mtime": _mtime(p)}
    return None


def meta() -> dict:
    root = data_root()
    ver = version_info()
    today = datetime.now().strftime("%Y-%m-%d")
    logs = eco_logs(limit=5)
    today_ran = any(r["ts"].startswith(today) for r in logs["rows"])
    sd = scripts_dir()
    return {"root": str(root), "root_exists": _looks_like_eco_root(root),
            "version": ver["version"], "score_model": "v3",
            "snapshot": datetime.now().isoformat(timespec="seconds"),
            "locks": lock_info(), "writer_today_ran": today_ran,
            "scripts_dir": str(sd) if sd else None,
            "search_cli_ready": bool(sd and (sd / "eco_note_query.py").is_file())}


def overview() -> dict:
    l2 = scan_l2()
    exps = scan_experiences()["items"]
    sk = scan_skills()
    logs = eco_logs(limit=300)
    cr = cron_info()
    ver = version_info()
    hist = score_history()
    aux = aux_dirs()
    by_type: dict[str, int] = {}
    for e in exps:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    mem = scan_l1()["memory"]
    latest_score = hist[-1] if hist else None
    return {
        "score": latest_score, "history": hist[-12:],
        "watermark": {"chars": mem["chars"], "quota": mem["quota"]},
        "counts": {
            "l1_entries": len(mem["entries"]), "l2": len(l2),
            "quarantine": len(aux["quarantine"]), "mem_pending": len(aux["pending"]),
            "exp_total": len(exps), "exp_draft": sum(1 for e in exps if e["status"] == "draft"),
            "exp_verified": sum(1 for e in exps if e["status"] == "verified"),
            "exp_by_type": by_type, "exp_pending": len(scan_experiences()["pending"]),
            "skills": len(sk["skills"]), "skills_unique": sk["unique"],
            "skills_archive": len(sk["archive"]), "skills_candidates": len(sk["candidates"]),
            "skills_declared": sk["declared"], "skills_three_ok": sk["three_ok"],
            "cron_eco": cr["eco_count"], "cron_total": cr["total"],
        },
        "gates": logs.get("latest", {}), "gate_counts": logs["counts"],
        "cron": {"eco_jobs": [j for j in cr["jobs"] if j["eco"]], "recent_fails": cr["recent_fails"],
                 "executions_7d": cr["executions_7d"], "jobs_snapshot": cr["jobs_snapshot"],
                 "fail_details": cr["fail_details"],
                 "all_jobs": cr["jobs"]},
        "candidates": scan_candidates(),
        "health_row": ver["health_rows"][0] if ver["health_rows"] else None,
        "gene": gene_log()[:5], "version": ver["version"],
    }
