#!/usr/bin/env python3
"""经验笔记本回扫并行执行器（2026-09-03 并行化：3 路并发，总时长 4-6h → ~2h）

从 queue.json 取会话，用 ThreadPoolExecutor 并行调用 eco_note_backfill.main()。
竞态隔离：
- 候选输出按会话分文件 cand-<session>.md（--out）
- processed 状态按会话分文件 .pc-<sid>.json（backfill 已改造）
- main(argv) 传参（不共享 sys.argv），线程安全

用法（cron：no_agent 无参数；后台：直接 python 本文件）：
    python eco_note_backfill_runall.py [--workers 3] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import eco_note_backfill as bf

QUEUE = bf.backfill_dir() / "queue.json"


def _pid_alive(pid: int) -> bool:
    """Windows 进程存活检查（OpenProcess；ACCESS_DENIED=进程活着，无句柄=已死）。
    防御：pid 超合理范围（>2^31 或 <2）视为死（stale 锁可回收；锁文件被写坏的情形）。"""
    import ctypes
    if not (0 < pid < 2**31):
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return ctypes.windll.kernel32.GetLastError() == 5


def acquire_lock() -> Path | None:
    """② v2.1.1 队列锁（#53 双执行器教训）：O_EXCL 创建 .runall.lock；
    已存在且 pid 存活 → 拒绝；pid 已死/stale → 清理重拿。"""
    bd = bf.backfill_dir()
    bd.mkdir(parents=True, exist_ok=True)
    lock_path = bd / ".runall.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lock_path
    except FileExistsError:
        try:
            pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = -1
        if pid > 0 and _pid_alive(pid):
            return None
        # stale 锁：清掉重拿（一次）
        try:
            lock_path.unlink()
        except OSError:
            return None
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return lock_path
        except FileExistsError:
            return None


def load_queue() -> dict:
    if not QUEUE.exists():
        return {"pending": [], "done": []}
    try:
        d = json.loads(QUEUE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"pending": [], "done": []}
    except (OSError, ValueError):
        return {"pending": [], "done": []}


def save_queue(q: dict) -> None:
    QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")


def work(sid: str, dry: bool) -> tuple[str, int]:
    """处理单个会话（独立输出文件，独立状态文件）。"""
    argv = ["--sessions", sid,
            "--max-cands", "150", "--max-tokens", "8000",
            "--out", str(bf.backfill_dir() / f"cand-{sid}.md")]
    if dry:
        argv.append("--dry-run")
    return sid, bf.main(argv)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lock = acquire_lock()
    if lock is None:
        print("⚠️ 检测到另一执行器在跑（.runall.lock），退出避免双活（#53 教训）")
        return 2
    try:
        return _run(args)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _run(args) -> int:
    q = load_queue()
    pending = q.get("pending", [])
    if not pending:
        print("回扫队列已空（全部会话处理完毕或队列未配置）")
        return 0
    print(f"并行回扫：{len(pending)} 个会话 / {args.workers} 路并发")

    done_ok = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(work, sid, args.dry_run): sid for sid in pending}
        for fut in futures:
            sid = futures[fut]
            try:
                s, rc = fut.result()
                if rc == 0 and not args.dry_run:
                    done_ok += 1
                    q = load_queue()
                    q["pending"] = [s for s in q.get("pending", []) if s != sid]
                    q.setdefault("done", []).append(sid)
                    save_queue(q)
                    print(f"✅ {sid} 完成（{done_ok}/{len(pending)}）")
                else:
                    print(f"✖ {sid} rc={rc}（{'dry-run 不标记' if args.dry_run else '不标记 done，下次续跑'}）")
            except Exception as e:
                print(f"✖ {sid} 异常: {e}（不标记 done，下次续跑）")
    remaining = load_queue().get("pending", [])
    if remaining:
        print(f"⚠️ 本批完成 {done_ok}/{len(pending)}，剩余 {len(remaining)} 个留待下次：{remaining}")
        return 1
    print(f"🏁 队列清空，全部 {done_ok} 个会话处理完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
