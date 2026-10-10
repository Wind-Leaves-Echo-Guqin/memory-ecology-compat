"""vector_cache — 向量缓存 shadow index（v2.4.0 S6）。

定位（memsearch「shadow index」同思想）：向量缓存一律是**可丢弃的派生物**——
原始记忆 = Markdown 文件（源真源），缓存坏/删/换模型 = 无损，随时重建。
绝不在缓存里放任何只存在于缓存的信息。

存储：<生态根>/memories/.vector_cache.db（SQLite BLOB；*.db 已被 .gitignore 覆盖）。
增量：条目级 content-hash（sha256(raw)）——未变跳过重嵌（memsearch / EverOS
content_sha256 同配方）；模型名变化自动整库失效（换模型=不同向量空间，不可混存）。

零 numpy：向量经 array('f') 与 SQLite BLOB 互转（embedding 后端返回归一化 float 列表，
余弦 = 纯 python 点积）。单写入方纪律适用：只有检索消费端写此库。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import sqlite3
from array import array
from pathlib import Path


def entry_text(e: dict) -> str:
    """参与向量化的条目文本：slug + 正文（与缓存失效哈希同源，改内容必失效）。"""
    return f"{e.get('slug', '')}\n{e.get('body', '')}"


def content_hash(e: dict) -> str:
    return hashlib.sha256(entry_text(e).encode("utf-8")).hexdigest()


def cosine(a: list[float], b: list[float]) -> float:
    """归一化向量的余弦 = 点积；任一为零向量返回 0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


class VectorCache:
    """条目级增量向量缓存（shadow index）。

    用法:
        vc = VectorCache(db_path, model_name)
        vecs = vc.sync(entries, embed_fn)   # {path_str: vec}，未变条目不重嵌
        vc.prune([e["path"] for e in entries])
        score = cosine(query_vec, vecs[str(e["path"])])
    """

    def __init__(self, db_path: Path, model_name: str):
        self.db_path = Path(db_path)
        self.model_name = model_name

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)  # 首次运行：数据根可能无此目录
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.execute("""CREATE TABLE IF NOT EXISTS vectors(
            path TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
            model TEXT NOT NULL, dim INTEGER NOT NULL,
            vec BLOB NOT NULL, updated_at TEXT)""")
        return conn

    def sync(self, entries: list[dict], embed_fn) -> dict[str, list[float]]:
        """取/建条目向量。content_hash 未变且模型一致 → 读缓存；否则重嵌并落库。

        单条失败（embedding 抛错/OSError）跳过该条不落库——缓存残缺可接受
        （shadow index 语义：缺了下次再建），绝不阻塞检索主流程。
        """
        out: dict[str, list[float]] = {}
        now = datetime.datetime.now().isoformat(timespec="seconds")
        conn = self._connect()
        try:
            stale_model = conn.execute(
                "SELECT COUNT(*) FROM vectors WHERE model<>?", (self.model_name,)).fetchone()[0]
            if stale_model:
                conn.execute("DELETE FROM vectors")  # 换模型 = 整库失效（向量空间不可混）
            for e in entries:
                key = str(e["path"])
                h = content_hash(e)
                row = conn.execute(
                    "SELECT content_hash, model, vec FROM vectors WHERE path=?",
                    (key,)).fetchone()
                if row and row[0] == h and row[1] == self.model_name:
                    arr = array("f")
                    arr.frombytes(row[2])
                    out[key] = list(arr)
                    continue
                try:
                    vec = [float(x) for x in embed_fn(entry_text(e))]
                except Exception:
                    continue  # 单条失败不落库不阻塞（shadow index 可残缺）
                if not vec:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO vectors(path, content_hash, model, dim, vec, updated_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (key, h, self.model_name, len(vec), array("f", vec).tobytes(), now))
                out[key] = vec
            conn.commit()
        finally:
            conn.close()
        return out

    def prune(self, keep_paths: list[str]) -> int:
        """删除源已消失的缓存行（保持镜像）。返回删除数。

        实现：Python 侧算差集（现存活路径 − keep）后按路径批量删——
        不用 `NOT IN (?...)`：SQLite 参数上限（旧版默认 999）会在千级条目
        （恰是本缓存的启用线）时报 `too many SQL variables`（2026-10-07 OCR 评审）。
        """
        keep = {str(p) for p in keep_paths}
        conn = self._connect()
        try:
            live = [row[0] for row in conn.execute("SELECT path FROM vectors")]
            stale = [(p,) for p in live if p not in keep]
            if stale:
                conn.executemany("DELETE FROM vectors WHERE path=?", stale)
            conn.commit()
            return len(stale)
        finally:
            conn.close()

    def stats(self) -> dict:
        """缓存概况（GUI/调试用；库不存在返回空）。"""
        if not self.db_path.exists():
            return {"rows": 0, "model": self.model_name}
        conn = self._connect()
        try:
            rows = conn.execute("SELECT COUNT(*), MIN(model) FROM vectors").fetchone()
            return {"rows": rows[0] or 0, "model": rows[1] or self.model_name}
        finally:
            conn.close()
