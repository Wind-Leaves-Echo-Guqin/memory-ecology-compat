#!/usr/bin/env python3
"""记忆生态·兼容版 · dsh 适配器（Python 侧）：dsh 会话实时报错 → 经验注入载荷。

由 dsh Extension SDK 插件（同目录 index.js）每回合 spawn 调用（fail-open：
本脚本任何异常都只导致"本轮不注入"，绝不阻塞宿主）。

数据流：
  ~/.dsh/sessions/**/session.jsonl.zstd（窗口内 mtime；UUID 在目录名，Q26）
    → zstd 解压取尾部 → 异常行提取（规则，零 LLM）
    → eco_note_error_query.rank（根因分层，Q2 修复版）
    → 注入文本（复用 Hermes 注入格式：ECHO_MARK 防回声 + 免责声明）
    → stdout JSON {"inject": str|null, ...}

输出契约（JSON 单行）：
  {"inject": "<文本>|null", "n_errors": int, "reason": "ok|no-recent-error|cooldown|no-hit",
   "episodes": [...], "ms": int}

状态：experiences/.dsh_inject_state.json（冷却时间戳 + 最近注入 episode，防重复骚扰）。
数据根：沿用 lib/config（MEMORY_ECOLOGY_ROOT 环境变量）——多宿主共享根时指向同一条根。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _locate_scripts_dir() -> Path:
    """定位核心 scripts 目录（环境变量优先，其次沿父目录链探测两种布局）。"""
    env = os.environ.get("ECO_SCRIPTS_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    for base in HERE.parents:  # package/python → dsh → adapters|integrations → 仓库根
        for rel in (("scripts",), ("src", "memory_ecology")):
            cand = base.joinpath(*rel)
            if cand.is_dir():
                return cand
    raise RuntimeError("无法定位生态 scripts 目录（可设 ECO_SCRIPTS_DIR）")


sys.path.insert(0, str(_locate_scripts_dir()))

import eco_note_error_query as errq  # noqa: E402
import eco_note_query as eq  # noqa: E402

# 与 Hermes 注入钩子（integrations/hermes/agent-hooks/eco_note_inject.py、
# eco_note_signals.py）同源常量——本地定义以免适配器依赖集成层模块
ECHO_MARK = "【经验参考】"
STATE_FILE = ".dsh_inject_state.json"
LINE_TAIL = 300          # 只看会话文件尾部行数
LINE_CAP = 400           # 单行截断（防超长 payload 撑爆注入）
MAX_STATE_EPISODES = 10  # 状态里记住的最近 episode 数


def _iter_session_files(sessions_root: Path, window_min: float, now: float) -> list[Path]:
    """窗口内有动静的会话文件（按 mtime 新→旧）。"""
    if not sessions_root.is_dir():
        return []
    out = []
    # Q26 修复（2026-09-06）：真实会话文件名为 session.jsonl.zstd（UUID 在目录名），
    # 原 glob "session-*.jsonl.zstd" 对 48/48 真机文件恒零匹配 → 注入通道从未可能命中
    for p in sessions_root.rglob("session*.jsonl.zstd"):
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if now - mt <= window_min * 60:
            out.append((mt, p))
    out.sort(reverse=True)
    # Q35 修复（2026-09-06）：只取最新活跃的一个会话——dsh 每回合持续写当前会话文件，
    # 最新 mtime ≈ 当前会话；原实现跨全部工作目录 slug 扫描，A 会话报错会注入 B 会话
    return [p for _, p in out[:1]]


def _decompress_tail(p: Path, tail_lines: int = LINE_TAIL) -> str:
    """zstd 解压并取尾部 N 行（会话文件可能很大，全文拼接前先截尾）。

    Q27 修复（2026-09-06）：真实会话是流式压缩（帧头无内容大小），一次性
    decompress 必抛 "could not determine content size in frame header"（真机
    抽样 0/20 成功）——改用 stream_reader(read_across_frames=True) 跨帧流式
    解压；带内容大小的单帧（旧数据）走 decompress 兜底。"""
    try:
        import io
        import zstandard
    except ImportError:
        return ""
    try:
        raw = p.read_bytes()
        dctx = zstandard.ZstdDecompressor()
        try:
            with dctx.stream_reader(io.BytesIO(raw), read_across_frames=True) as r:
                text = r.read().decode("utf-8", errors="replace")
        except (TypeError, zstandard.ZstdError):
            text = dctx.decompress(raw).decode("utf-8", errors="replace")
    except Exception:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-tail_lines:])


def find_recent_errors(sessions_root: Path, window_min: float, now: float | None = None) -> list[str]:
    """提取窗口内的报错行（规则匹配异常类名/Traceback，零 LLM）。"""
    now = now if now is not None else time.time()
    errors: list[str] = []
    for p in _iter_session_files(sessions_root, window_min, now):
        tail = _decompress_tail(p)
        if not tail:
            continue
        for line in tail.splitlines():
            if ECHO_MARK in line:
                continue  # Q35（2026-09-06）：注入文本若被写回会话文件，不当作新报错（防自激）
            if errq.EXC_RE.search(line) or "Traceback" in line:
                errors.append(line.strip()[:LINE_CAP])
    return errors


def _load_state(exp_dir: Path) -> dict:
    try:
        return json.loads((exp_dir / STATE_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(exp_dir: Path, state: dict) -> None:
    try:
        (exp_dir / STATE_FILE).write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 状态写失败不阻断注入


def build_injection(errors: list[str], top: int = 2) -> tuple[str | None, list[str]]:
    """报错行 → (注入文本, episode 列表)。无命中返回 (None, [])。"""
    if not errors:
        return None, []
    query = "\n".join(errors)[:2000]
    hits = errq.rank(query, top=top)
    if not hits:
        return None, []
    lines = [f"{ECHO_MARK}(只读参考, 可忽略) 最近出错，以下相关经验可能有用（按根因匹配，未必相关）:"]
    episodes = []
    for h in hits:
        meta = h["entry"]["meta"]
        fields = h["entry"]["fields"]
        note = (fields.get("action") or fields.get("cause") or "")[:100]
        lines.append(f"- [{h['path']} | {meta.get('type','?')} | {meta.get('status','?')}] "
                     f"{fields.get('title','')[:60]} → {note}")
        if h["episode"]:
            episodes.append(h["episode"])
    return "\n".join(lines), episodes


def run(sessions_root: Path, window_min: float = 10.0, cooldown_min: float = 15.0,
        top: int = 2, now: float | None = None) -> dict:
    """主流程（可注入 now 供测试）。返回输出契约 dict。"""
    t0 = time.time()
    now = now if now is not None else time.time()
    exp_dir = eq.EXP_DIR
    state = _load_state(exp_dir)

    errors = find_recent_errors(sessions_root, window_min, now)
    if not errors:
        return {"inject": None, "n_errors": 0, "reason": "no-recent-error",
                "episodes": [], "ms": _ms(t0)}

    # 冷却：窗口内已注入过则静默（防每回合重复骚扰）
    last_ts = state.get("last_inject_ts", 0)
    if last_ts and (now - last_ts) < cooldown_min * 60:
        return {"inject": None, "n_errors": len(errors), "reason": "cooldown",
                "episodes": [], "ms": _ms(t0)}

    inject, episodes = build_injection(errors, top=top)
    if inject is None:
        return {"inject": None, "n_errors": len(errors), "reason": "no-hit",
                "episodes": [], "ms": _ms(t0)}

    # 跳过最近已注入过的 episode（冷却刚过的场景不重复推同一条）
    seen = set(state.get("last_episodes", []))
    if episodes and all(ep in seen for ep in episodes):
        return {"inject": None, "n_errors": len(errors), "reason": "episodes-seen",
                "episodes": episodes, "ms": _ms(t0)}

    _save_state(exp_dir, {
        "last_inject_ts": now,
        "last_episodes": (episodes + state.get("last_episodes", []))[:MAX_STATE_EPISODES],
    })
    return {"inject": inject, "n_errors": len(errors), "reason": "ok",
            "episodes": episodes, "ms": _ms(t0)}


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dsh 会话实时报错 → 经验注入载荷（只读+状态文件）")
    ap.add_argument("--sessions-root", default=os.environ.get(
        "DSH_SESSIONS_ROOT", str(Path.home() / ".dsh" / "sessions")))
    ap.add_argument("--window-min", type=float, default=10.0)
    ap.add_argument("--cooldown-min", type=float, default=15.0)
    ap.add_argument("--top", type=int, default=2)
    args = ap.parse_args(argv)
    out = run(Path(args.sessions_root), args.window_min, args.cooldown_min, args.top)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
