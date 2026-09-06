# -*- coding: utf-8 -*-
"""eco_review.py 自测：语法检查 + fixture dry-run + 实际执行验证。"""
import ast
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from lib.config import hermes_root

SCRIPT = hermes_root() / "scripts" / "eco_review.py"

# ---------- 1. 语法检查 ----------
src = SCRIPT.read_text(encoding='utf-8')
ast.parse(src)
print("[1] AST parse OK; BOM literal count =", src.count('\ufeff'))
assert src.count('\ufeff') == 2, 'rewrite_status 里应有两个 BOM 字面量'

# ---------- 2. 构造 fixture ----------
tmp = Path(tempfile.mkdtemp(prefix='eco_review_selftest_'))
mem = tmp / 'memories'
detail = mem / 'detail'
quar = mem / 'quarantine'
arch = mem / 'archive'
detail.mkdir(parents=True)
(quar / '2026-01-01').mkdir(parents=True)
(quar / '2026-07-01').mkdir(parents=True)
arch.mkdir(parents=True)

today = date.today()


def write_detail(name, type_, status, lv, body):
    (detail / name).write_text(
        f"---\nname: {name}\ntype: {type_}\nstatus: {status}\n"
        f"last_verified: {lv}\n---\n{body}\n", encoding='utf-8')


write_detail('act-over-1.md', 'semantic', 'active',
             (today - timedelta(days=200)).isoformat(),
             '训练任务固定使用 .venv312 解释器')
write_detail('act-over-2.md', 'episodic', 'active',
             (today - timedelta(days=100)).isoformat(),
             '训练任务固定使用 .venv312 解释器')
write_detail('dorm-over.md', 'lesson', 'dormant',
             (today - timedelta(days=240)).isoformat(),
             '旧课程记忆：Windows glob 需要去重')
write_detail('fresh.md', 'semantic', 'active',
             (today - timedelta(days=5)).isoformat(),
             '新记忆内容示例')

old_ts = datetime.combine(today - timedelta(days=150), datetime.min.time()).timestamp()
fresh_ts = datetime.combine(today - timedelta(days=10), datetime.min.time()).timestamp()
for name in ('q-old-1.md', 'q-old-2.md'):
    f = quar / '2026-01-01' / name
    f.write_text(name, encoding='utf-8')
    os.utime(f, (old_ts, old_ts))
qf = quar / '2026-07-01' / 'q-fresh.md'
qf.write_text('q-fresh', encoding='utf-8')
os.utime(qf, (fresh_ts, fresh_ts))

print('[2] fixture 就绪:', tmp)

# ---------- 3. dry-run ----------
r = subprocess.run([sys.executable, str(SCRIPT), '--dry-run', '--detail', str(detail)],
                   capture_output=True, text=True, encoding='utf-8')
print('[3] ---- dry-run stdout ----')
print(r.stdout)
if r.stderr:
    print('[3] ---- dry-run stderr ----')
    print(r.stderr)
assert r.returncode == 0, f'dry-run exit={r.returncode}'
out = r.stdout
assert '[mark_dormant] act-over-1' in out
assert '[mark_dormant] act-over-2' in out
assert '[archive] dorm-over' in out
assert '[merge_candidate]' in out
assert 'act-over-1 <-> act-over-2' in out
assert '[quarantine_cleanup]' in out
assert '2026-01-01' in out and 'q-old-1.md' in out
assert 'q-fresh' not in out.split('quarantine 清理')[1].split('合并候选')[0] or 'q-fresh' not in out
# dry-run 不得改动任何文件/数据库
assert (detail / 'act-over-1.md').read_text(encoding='utf-8').count('status: active') == 1
assert (detail / 'dorm-over.md').exists()
assert not (arch / 'dorm-over.md').exists()
assert not (arch / '2026-01-01').exists()
assert not (mem / 'gate_log').exists()
assert not (mem / 'eco.db').exists()
print('[3] dry-run 验证通过：仅打印，未修改/移动任何文件、未写 db/gate_log')

# ---------- 4. 实际执行 ----------
r2 = subprocess.run([sys.executable, str(SCRIPT), '--detail', str(detail)],
                    capture_output=True, text=True, encoding='utf-8')
print('[4] ---- 实际执行 stdout ----')
print(r2.stdout)
if r2.stderr:
    print('[4] ---- 实际执行 stderr ----')
    print(r2.stderr)
assert r2.returncode == 0, f'real-run exit={r2.returncode}'

# 降级：active→dormant 原地重写，文件仍在 detail
assert 'status: dormant' in (detail / 'act-over-1.md').read_text(encoding='utf-8')
assert 'status: dormant' in (detail / 'act-over-2.md').read_text(encoding='utf-8')
assert (detail / 'act-over-1.md').exists()
# 归档：dormant 超期 → 移入 archive
assert not (detail / 'dorm-over.md').exists()
assert (arch / 'dorm-over.md').exists()
# quarantine 清理：保留日期子目录
assert (arch / '2026-01-01' / 'q-old-1.md').exists()
assert (arch / '2026-01-01' / 'q-old-2.md').exists()
assert (quar / '2026-07-01' / 'q-fresh.md').exists()  # 未超期保留
assert (quar / '2026-01-01' / 'q-old-1.md').exists() is False
# eco.db
dbp = mem / 'eco.db'
assert dbp.exists()
conn = sqlite3.connect(str(dbp))
rows = conn.execute('SELECT action, COUNT(*) FROM review_log GROUP BY action').fetchall()
conn.close()
print('[4] review_log 分组:', dict(rows))
counts = dict(rows)
assert counts.get('mark_dormant') == 2
assert counts.get('archive') == 1
assert counts.get('merge_candidate') == 1
assert counts.get('quarantine_cleanup') == 2
# gate_log 清单
gl = mem / 'gate_log' / f'review-{today.isoformat()}.md'
assert gl.exists()
gtext = gl.read_text(encoding='utf-8')
for key in ('## 一、过期复核清单', '## 二、碎片合并候选', '## 三、quarantine 清理清单',
            'act-over-1', 'dorm-over', 'q-old-1'):
    assert key in gtext, f'gate_log 缺少: {key}'
print('[4] 实际执行验证通过：降级/归档/清理/DB/gate_log 均符合预期')

shutil.rmtree(tmp, ignore_errors=True)
print()
print('ALL SELF-TESTS PASSED')
