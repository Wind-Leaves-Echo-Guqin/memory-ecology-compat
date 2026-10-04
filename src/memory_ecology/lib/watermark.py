"""watermark — 水位线分页统一层（2026-10-04，类型 C 单源裂缝收敛）。

此前 signals / extract / note 三处各自实现 `timestamp > ? ORDER BY timestamp ASC LIMIT ?`
并把水位线推进到本批 max timestamp——当 LIMIT 截断（或内存分批切分）恰好落在同一
timestamp 的消息中间时，下轮 `timestamp > max` 会跳过同 timestamp 的剩余消息（跳窗）。

复合键方案：水位线 = (timestamp, id)，SQL 改 `(timestamp > ?) OR (timestamp = ? AND id > ?)`
ORDER BY timestamp, id。旧格式（纯数字标量）兼容读取（id 视为 -1，同 ts 消息从头重扫；
重复扫描由各调用方的幂等消费兜底——宁可重复，不可跳过）。

零依赖（stdlib only）。
"""
from __future__ import annotations

INF_TS = float("inf")


def parse_watermark(raw) -> tuple[float, int]:
    """兼容解析水位线：支持 (ts, id) 元组、"ts|id" 字符串、"(ts, id)" 字符串、旧版标量。

    None/空 → (-inf, -1)（从头扫）；非法数值按损坏处理交调用方兜底（本函数抛 ValueError）。
    """
    if raw is None:
        return (-INF_TS, -1)
    if isinstance(raw, tuple):
        return (float(raw[0]), int(raw[1]))
    s = str(raw).strip().strip("() ")
    if not s:
        return (-INF_TS, -1)
    if "|" in s:
        ts, _, i = s.partition("|")
        return (float(ts), int(i))
    if "," in s:
        ts, _, i = s.partition(",")
        return (float(ts), int(i or -1))
    return (float(s), -1)


def format_watermark(wm: tuple[float, int]) -> str:
    """落盘格式："ts|id"（parse_watermark 可往返；旧标量读取端需走 parse）。"""
    ts, i = parse_watermark(wm)
    return f"{ts}|{i}"


def where_clause(watermark, ts_col: str = "timestamp", id_col: str = "id") -> tuple[str, list]:
    """生成复合键 WHERE 片段与参数（调用方拼入自己的 SQL，注意整体加括号）。

    例: sql = f"... WHERE ({wc}) AND role='user' ORDER BY timestamp ASC, id ASC LIMIT ?"
    """
    ts0, id0 = parse_watermark(watermark)
    return (f"({ts_col} > ?) OR ({ts_col} = ? AND {id_col} > ?)", [ts0, ts0, id0])


def next_watermark(rows, ts_idx: int, id_idx: int) -> tuple[float, int]:
    """本批末行 → 新水位线；空批返回 (-inf, -1)（调用方不应推进）。"""
    if not rows:
        return (-INF_TS, -1)
    return (float(rows[-1][ts_idx]), int(rows[-1][id_idx]))
