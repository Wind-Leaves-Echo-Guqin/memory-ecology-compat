#!/usr/bin/env python3
"""检索 CLI 子进程包装（memory-ecology GUI · 观测舱）。

复用三个只读检索引擎（零逻辑复制）：eco_note_query / eco_note_error_query / eco_search。
fail-open（设计稿 §5）：任何失败返回 {'ok': False, 'error': ...}，绝不抛出阻塞页面。
超时 25s；cwd=scripts 目录（lib.config 派生依赖）。

v0.3（T1）：subprocess 注入 PYTHONUTF8/PYTHONIOENCODING——Windows 下 GUI 若从
旧版 cmd/资源管理器启动，环境无 UTF-8 声明，CLI stdout 走 GBK，GUI 按 UTF-8
解码 → 中文全坏。双保险：env 注入 + 三个 CLI 头部已加 stdout.reconfigure。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from collector import scripts_dir

_TIMEOUT = 25


def _utf8_env() -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(args: list[str], timeout: int = _TIMEOUT) -> tuple[bool, str]:
    sd = scripts_dir()
    if not sd:
        return False, "未找到检索脚本目录（可设 --scripts-dir 或 ECO_SCRIPTS_DIR）"
    # pythonw（GUI 无 console）派生子进程必须禁新 console，否则每次检索弹黑窗抢焦点
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    try:
        r = subprocess.run([sys.executable, *args], cwd=str(sd), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           env=_utf8_env(), creationflags=flags)
        return True, (r.stdout or "") + (("\n" + r.stderr) if r.returncode != 0 and r.stderr else "")
    except subprocess.TimeoutExpired:
        return False, f"检索超时（>{timeout}s）"
    except OSError as e:
        return False, f"子进程启动失败：{e}"


def _parse_note(text: str) -> list[dict]:
    """eco_note_query 文本行 → 结构化：'  - id | type | status | trigger | 证:…'"""
    out = []
    for line in text.splitlines():
        m = re.match(r"\s*-\s*([\w-]+)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(.*?)\s*(?:\|\s*证[:：](.*))?$",
                     line)
        if m:
            out.append({"id": m.group(1), "type": m.group(2), "status": m.group(3),
                        "trigger": m.group(4).strip(), "evidence": (m.group(5) or "").strip()})
    return out


def _parse_eco(text: str) -> list[dict]:
    """eco_search 文本 → 结构化：'  name ver [st/fa] 被引N desc' + '      ├ 章节: …'

    v0.3：版本列兼容"未声明"（原 '?'）与任意非 v 前缀写法，避免整行漏解析。
    """
    out: list[dict] = []
    cur = None
    for line in text.splitlines():
        m = re.match(r"\s{2}([\w.-]+)\s+(\S+)\s+\[(\w+)/(\w+)\]\s+被引(\d+)\s+(.*)$", line)
        if m:
            cur = {"name": m.group(1), "version": m.group(2), "status": m.group(3),
                   "fate": m.group(4), "indeg": int(m.group(5)), "desc": m.group(6).strip(),
                   "sections": ""}
            out.append(cur)
        elif cur is not None:
            cm = re.match(r"\s+[├└].*?章节[:：]\s*(.*)$", line)
            if cm:
                cur["sections"] += (("；" if cur["sections"] else "") + cm.group(1).strip())
    return out


def search(kind: str, q: str) -> dict:
    q = (q or "").strip()
    if not q:
        return {"ok": False, "error": "空查询"}
    if kind == "note":
        ok, out = _run(["eco_note_query.py", q, "--top", "8"])
        hits = _parse_note(out)
        return {"ok": ok, "kind": kind, "hits": hits,
                "raw": out if not hits else None, "error": None if ok else out}
    if kind == "error":
        ok, out = _run(["eco_note_error_query.py", q, "--format", "json"])
        try:
            data = json.loads(out[out.index("{"):out.rindex("}") + 1])
            return {"ok": ok, "kind": kind, "exc": data.get("query_exc"),
                    "hits": data.get("hits", []), "error": None}
        except (ValueError, IndexError):
            ok2, out2 = _run(["eco_note_error_query.py", q])
            return {"ok": ok2, "kind": kind, "hits": [], "raw": out2, "error": None if ok2 else out2}
    if kind == "eco":
        ok, out = _run(["eco_search.py", q])
        hits = _parse_eco(out)
        return {"ok": ok, "kind": kind, "hits": hits,
                "raw": out if not hits else None, "error": None if ok else out}
    return {"ok": False, "error": f"未知检索类型 {kind}"}
