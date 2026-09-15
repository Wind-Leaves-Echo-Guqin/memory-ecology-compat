#!/usr/bin/env python3
"""生态急救箱（v0.3 · F）—— GUI 服务死了也能用的自检修复工具。

双入口：
  1. 桌面「生态急救箱.cmd」→ python first_aid.py --fix（诊断+修复+重启 GUI 服务）
  2. GUI 顶栏「急救箱」按钮 → /api/firstaid（只诊断，修复走轻确认闸门调 /api/action/firstaid_fix）

自检范围（F1）：服务与窗口 / 数据完整性 / 缓存与锁。
生产 cron 领地（F3）：只诊断并给出修复命令，绝不动手。

用法:
  python first_aid.py            # 只诊断（中文进度 + 报告落盘 firstaid_report.txt）
  python first_aid.py --fix      # 诊断 + 自动修复（清陈旧锁/重建过期索引/换端口重启服务）
  python first_aid.py --json     # 机读输出（供 /api/firstaid 调用）
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
GUI_DIR = HERE
REPORT = GUI_DIR / "firstaid_report.txt"
PORT_DEFAULT = 8788


def data_root() -> Path:
    env = os.environ.get("MEMORY_ECOLOGY_ROOT")
    if env:
        return Path(env)
    home = Path.home()
    for cand in (home / "AppData" / "Local" / "hermes", home / ".hermes"):
        if cand.is_dir() and ((cand / "memories").is_dir() or (cand / "scripts" / "lib").is_dir()):
            return cand
    return HERE.parents[1]  # 兜底：仓库根


def scripts_dir() -> Path:
    """检索 CLI 目录：ECO_SCRIPTS_DIR > 数据根 scripts/ > 发布树 src/memory_ecology/。"""
    env = os.environ.get("ECO_SCRIPTS_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    for cand in (data_root() / "scripts", HERE.parents[1] / "src" / "memory_ecology"):
        if (cand / "eco_note_query.py").is_file():
            return cand
    return data_root() / "scripts"


_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _pids_listening(port: int) -> list[int]:
    """Windows：netstat 找 LISTENING 端口的 pid。"""
    pids = []
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=10, encoding="utf-8", errors="replace",
                             creationflags=_NO_WINDOW).stdout or ""
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1] == f"127.0.0.1:{port}" and parts[3] == "LISTENING":
                pid = int(parts[4])
                if pid and pid not in pids:
                    pids.append(pid)
    except Exception:
        pass
    return pids


def _is_our_server(port: int) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/meta", timeout=1.5) as r:
            return "version" in json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:
        return False


def _pid_name(pid: int) -> str:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True,
                             text=True, timeout=10, encoding="utf-8", errors="replace",
                             creationflags=_NO_WINDOW).stdout or ""
        for line in out.splitlines():
            if line.strip().startswith("="):
                continue
            parts = line.split()
            if parts and parts[-1].isdigit() and int(parts[-1]) == pid:
                return parts[0]
    except Exception:
        pass
    return "?"


def diagnose(fix: bool = False) -> dict:
    """自检主体。返回 {checks: [{id,name,status,detail,action}], fixes: [...]}。"""
    checks: list[dict] = []
    fixes: list[str] = []

    def add(cid, name, ok, detail="", action=""):
        checks.append({"id": cid, "name": name,
                       "status": "ok" if ok else ("fixed" if action and fix else "fail"),
                       "detail": detail, "action": action if not fix else ("" if ok else action)})
        return ok

    root = scripts_dir()
    port = PORT_DEFAULT

    # 1. 端口占用 / 旧进程
    pids = _pids_listening(port)
    if not pids:
        add("port", f"端口 {port} 可用", True, "当前无监听进程")
    elif _is_our_server(port):
        add("port", f"端口 {port} 已有本观测舱服务", True,
            f"pid={pids}，浏览器打开 http://127.0.0.1:{port} 即可；重启服务会先结束旧进程")
    else:
        names = [f"{p}({_pid_name(p)})" for p in pids]
        if fix:
            for p in pids:
                subprocess.run(["taskkill", "/F", "/PID", str(p)], capture_output=True, timeout=10, creationflags=_NO_WINDOW)
                fixes.append(f"已结束占用端口 {port} 的无关进程 pid={p}")
            time.sleep(0.8)
            add("port", f"端口 {port}", not _pids_listening(port), "原被外部进程占用", f"已清理：{names}")
        else:
            add("port", f"端口 {port} 被外部进程占用", False, f"pid：{names}", f"结束进程 {pids} 或 --port 换端口")

    # 2. pythonw 可用性
    pyw = _pythonw()
    add("pythonw", "pythonw（无窗口启动）可用", bool(pyw), pyw or "将回退 python 控制台窗口")

    # 3. 数据根与关键文件
    dr = data_root()
    key_files = [dr / "memories" / "MEMORY.md", dr / "memories" / "USER.md",
                 dr / "eco.db", dr / "cron" / "jobs.json", dr / "VERSION.md"]
    missing = [str(p) for p in key_files if not p.is_file()]
    add("dataroot", "数据根与关键文件", not missing,
        f"根={dr}" + ("" if not missing else f"；缺失：{missing}"),
        "缺失为严重损坏，请从备份恢复（见体检报告 §备份）")

    # 4. 数据库完整性
    for db in (dr / "eco.db", dr / "cron" / "executions.db"):
        if not db.is_file():
            add(f"db-{db.name}", f"{db.name} 完整性", False, "文件不存在", "从备份恢复或跑一次对应门脚本重建")
            continue
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=3)
            try:
                row = con.execute("PRAGMA quick_check").fetchone()
                res = (row[0] if row else "missing")
            finally:
                con.close()
            add(f"db-{db.name}", f"{db.name} 完整性", res == "ok", f"quick_check={res}")
        except sqlite3.Error as e:
            add(f"db-{db.name}", f"{db.name} 完整性", False, f"打开失败：{e}", "数据库损坏：从备份恢复")

    # 5. 检索 CLI 存在性
    sd = scripts_dir()
    clis = ["eco_search.py", "eco_note_query.py", "eco_note_error_query.py"]
    miss_cli = [c for c in clis if not (sd / c).is_file()] if sd else clis
    add("cli", "检索 CLI 三件", sd is not None and not miss_cli,
        f"scripts={sd}" + ("" if not miss_cli else f"；缺失：{miss_cli}"),
        "缺失请从兼容版仓库 scripts/ 复制对应文件")

    # 6. 检索索引新鲜度（>1 天自动重建）
    idx = dr / "ecosystem.db"
    if idx.is_file():
        age_h = (time.time() - idx.stat().st_mtime) / 3600
        if age_h > 24:
            did = False
            if fix:
                r = subprocess.run([sys.executable, str(sd / "eco_search.py"), "--rebuild"],
                                   cwd=str(sd), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=120,
                                   creationflags=_NO_WINDOW)
                did = r.returncode == 0
                fixes.append(f"检索索引已重建（age {age_h:.0f}h，exit={r.returncode}）")
            add("index", "检索索引新鲜度", did, f"索引已 {age_h:.0f}h 未重建", "运行 eco_search.py --rebuild")
        else:
            add("index", "检索索引新鲜度", True, f"{age_h:.1f}h 前重建，正常")
    else:
        did = False
        if fix:
            r = subprocess.run([sys.executable, str(sd / "eco_search.py"), "--rebuild"],
                               cwd=str(sd), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=120,
                               creationflags=_NO_WINDOW)
            did = r.returncode == 0
            fixes.append("检索索引缺失，已重建")
        add("index", "检索索引存在", did, "ecosystem.db 不存在（检索不可用）", "运行 eco_search.py --rebuild")

    # 7. 陈旧锁清理（仅 .lock，先列后删）
    stale_locks = []
    memdir = dr / "memories"
    if memdir.is_dir():
        for p in memdir.glob("*.lock"):
            if (time.time() - p.stat().st_mtime) > 2 * 3600:
                stale_locks.append(p)
    if stale_locks:
        if fix:
            for p in stale_locks:
                try:
                    p.unlink()
                    fixes.append(f"已清理陈旧锁 {p.name}（mtime>2h）")
                except OSError:
                    pass
            add("locks", "陈旧锁文件", not list(memdir.glob("*.lock")),
                f"发现 {len(stale_locks)} 个陈旧锁", "已清理（仅 .lock 文件）")
        else:
            add("locks", "陈旧锁文件", False,
                "、".join(p.name for p in stale_locks), "删除陈旧 .lock（写入方未被卡死时安全）")
    else:
        add("locks", "锁文件", True, "无陈旧锁（2h 内锁=写入方正常活动）")

    # 8. 体检报告存在性
    rep = Path.home() / ".memory-ecology" / "designs" / "生态体检报告-v1.1.md"
    if rep.is_file():
        age_h = (time.time() - rep.stat().st_mtime) / 3600
        add("report", "体检报告", True, f"距上次体检 {age_h:.0f}h")
    else:
        add("report", "体检报告", False, "未找到体检报告",
            "运行 python scripts/eco_health_check.py 生成")

    # 9. 生产 cron 领地（F3：只诊断给命令，绝不动手）
    cron_errs = []
    jp = dr / "cron" / "jobs.json"
    if jp.is_file():
        try:
            data = json.loads(jp.read_text(encoding="utf-8", errors="replace"))
            jobs = data if isinstance(data, list) else data.get("jobs", [])
            cron_errs = [j for j in jobs if j.get("last_status") == "error"]
        except json.JSONDecodeError:
            cron_errs = [{"id": "?", "name": "jobs.json 解析失败"}]
    add("cron", "生产 cron 领地（只诊断）", not cron_errs,
        (f"{len(cron_errs)} 个任务 last_status=error："
         + "、".join(j.get("name", "?") for j in cron_errs)) if cron_errs else "全部瞬时正常",
        "修复命令：hermes cron run <id> 重跑 / hermes cron edit <id> --provider X --model Y 固定模型（GUI cron 视图可复制）")

    return {"checks": checks, "fixes": fixes, "root": str(dr),
            "ts": datetime.now().isoformat(timespec="seconds"),
            "all_ok": all(c["status"] in ("ok", "fixed") for c in checks)}


def _pythonw() -> str:
    for exe in (sys.executable,):
        w = Path(exe).with_name("pythonw.exe")
        if w.is_file():
            return str(w)
    return ""


def restart_gui(fix: bool = True) -> str:
    """重启 GUI 服务：杀本仓旧进程 → pythonw eco_gui.py 分离启动 → 开窗。"""
    msg = []
    pyw = _pythonw()
    exe = pyw or sys.executable
    # 旧的本观测舱进程
    pids = _pids_listening(PORT_DEFAULT)
    for p in pids:
        if _is_our_server(PORT_DEFAULT):
            subprocess.run(["taskkill", "/F", "/PID", str(p)], capture_output=True, timeout=10, creationflags=_NO_WINDOW)
            msg.append(f"已结束旧服务 pid={p}")
    time.sleep(0.8)
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    subprocess.Popen([exe, str(GUI_DIR / "eco_gui.py")], cwd=str(GUI_DIR),
                     creationflags=flags | _NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    msg.append("GUI 服务已重新启动（pythonw 分离进程）")
    for _ in range(20):
        time.sleep(0.5)
        if _is_our_server(PORT_DEFAULT):
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{PORT_DEFAULT}")
            msg.append(f"已开窗 http://127.0.0.1:{PORT_DEFAULT}")
            break
    return "\n".join(msg)


def main() -> int:
    ap = argparse.ArgumentParser(description="生态急救箱：自检 → 修复 → 重启服务 → 开窗")
    ap.add_argument("--fix", action="store_true", help="自动执行修复（默认只诊断）")
    ap.add_argument("--json", action="store_true", help="机读 JSON 输出")
    ap.add_argument("--no-restart", action="store_true", help="修复后不重启 GUI 服务")
    a = ap.parse_args()

    print("╭──────────────────────────────────────────╮")
    print("│ 生态急救箱 · 自检中…                      │")
    print("╰──────────────────────────────────────────╯")
    result = diagnose(fix=a.fix)
    for c in result["checks"]:
        mark = "✅" if c["status"] in ("ok", "fixed") else ("🔧" if c["status"] == "fixed" else "❌")
        print(f"{mark} {c['name']}：{c['detail']}")
        if c["status"] == "fail" and c["action"]:
            print(f"    ↳ 处置：{c['action']}")
    for f in result["fixes"]:
        print(f"🔧 {f}")
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n诊断报告已落盘：{REPORT}")
    verdict = "全部通过" if result["all_ok"] else "存在问题（见上）"
    print(f"结论：{verdict}")
    if a.json:
        print(json.dumps(result, ensure_ascii=False))
    if a.fix and not a.no_restart:
        print("\n—— 重启 GUI 服务 ——")
        print(restart_gui())
    return 0 if result["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
