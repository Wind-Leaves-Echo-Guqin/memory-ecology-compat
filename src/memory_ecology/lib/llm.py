"""LLM 调用公共函数（memory-ecology lib）。

complete() 纯函数：默认走 deepseek API，client 可注入（测试用 fake）。
密钥解析顺序（Q32 修复 2026-09-06，与文档口径对齐）：
  显式 api_key 参数 → 环境变量 MEMORY_ECOLOGY_API_KEY → 数据根 .env 的 DEEPSEEK_API_KEY
不做类层次——13 脚本中真调 LLM 的仅 3-5 个，不值得客户端框架。周熔断逻辑留各脚本（成本护栏属业务配置）。
"""
import json
import os
import re
import urllib.request

from . import config

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_TIMEOUT = 90


def complete(prompt: str, *, client=None, model: str = DEFAULT_MODEL,
             base_url: str = DEFAULT_BASE_URL, api_key: str | None = None,
             max_tokens: int = 800, temperature: float = 0.1,
             timeout: int = DEFAULT_TIMEOUT) -> str:
    """单轮 LLM 补全，返回文本内容。client 可注入（测试 fake 签名：
    client(prompt, model=..., max_tokens=..., temperature=...) -> str）。"""
    if client is not None:
        return client(prompt, model=model, max_tokens=max_tokens, temperature=temperature)
    key = (api_key
           or os.environ.get("MEMORY_ECOLOGY_API_KEY")
           or config.env_key("DEEPSEEK_API_KEY"))
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return d["choices"][0]["message"]["content"]


# ── 结构化调用层（P1 单源收编 2026-10-04：能力上收自 eco_note.llm_extract）──
# 背景：lib/llm 原为 30 行裸封装，重试/sentinel/JSON 提取/usage 记账留在
# eco_note 本地——"公共库落后于实际需求"的类型 B 单源裂缝。本层把抗压实测的
# 传输语义收编为公共能力（类型 B 修复：先升级公共库，再让调用方收编）。

class MalformedResponseError(RuntimeError):
    """HTTP 200 但响应不可用：body 非 JSON / choices 为空 / content 非字符串。"""


class TruncatedResponseError(RuntimeError):
    """finish_reason=length 且重试耗尽——截断语义 ≠ 空结果，调用方不应推进水位线。"""


def _extract_json(content: str):
    """三级 JSON 提取：整体解析 → 剥 ```json 围栏 → 平衡括号扫描（B3-① 口径）。"""
    def _try(cand):
        try:
            d = json.loads(cand)
            return d
        except Exception:
            return None
    r = _try(content)
    if r is not None:
        return r
    m = re.search(r"```(?:json)?\s*(.*?)```", content, re.S)
    if m:
        r = _try(m.group(1))
        if r is not None:
            return r
    for start, ch in enumerate(content):
        if ch != "[":
            continue
        depth = 0
        for pos in range(start, len(content)):
            if content[pos] == "[":
                depth += 1
            elif content[pos] == "]":
                depth -= 1
                if depth == 0:
                    r = _try(content[start:pos + 1])
                    if r is not None:
                        return r
                    break
    return None


def complete_json(prompt: str, *, system: str | None = None, client=None,
                  model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL,
                  api_key: str | None = None, max_tokens: int = 800,
                  temperature: float = 0.1, timeout: int = DEFAULT_TIMEOUT,
                  retries: int = 2, require=None, on_usage=None) -> list | dict:
    """单轮调用 + JSON 解析 + 抗压语义，返回解析后的对象（require=list 时须为数组）。

    - finish_reason=length（reasoning 模型吃满 max_tokens）→ max_tokens 翻倍重试；
      重试耗尽仍截断/解析为空 → TruncatedResponseError（截断 ≠ 无结果，调用方不应推进）
    - HTTP 200 但 body 非 JSON / choices 空 / content 非字符串 → MalformedResponseError
    - on_usage(resp)：成功请求的 usage 回调（记账用；无 usage 字段自动跳过）
    require：期望类型（list/dict），解析结果不符按未提取处理（返回 None 语义 → 触发截断判定）。
    """
    if client is not None:
        content = client(prompt, model=model, max_tokens=max_tokens, temperature=temperature)
        parsed = _extract_json(content)
        if require is not None and not isinstance(parsed, require):
            parsed = None
        if parsed is None:
            raise MalformedResponseError("client 返回内容无法解析为期望 JSON 结构")
        return parsed

    key = (api_key or os.environ.get("MEMORY_ECOLOGY_API_KEY")
           or config.env_key("DEEPSEEK_API_KEY"))
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]

    def _post(tokens: int):
        body = json.dumps({"model": model, "messages": messages,
                           "max_tokens": tokens, "temperature": temperature}).encode("utf-8")
        req = urllib.request.Request(
            base_url, data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        try:
            d = json.loads(raw)
        except Exception:
            raise MalformedResponseError(
                f"HTTP 200 但 body 非 JSON（疑似网关/代理异常响应）：{raw[:60]!r}")
        if not d.get("choices"):
            raise MalformedResponseError(f"HTTP 200 但 choices 为空：{str(d)[:60]}")
        if callable(on_usage):
            try:
                on_usage(d)
            except Exception:
                pass  # 记账失败不影响主流程
        msg = d["choices"][0].get("message") or {}
        content = msg.get("content") or ""
        if not isinstance(content, str):
            raise MalformedResponseError(f"content 非字符串（{type(content).__name__}）")
        return d, content

    d, content = _post(max_tokens)
    finish = d["choices"][0].get("finish_reason")
    if finish == "length" and retries > 0:
        return complete_json(prompt, system=system, model=model, base_url=base_url,
                             api_key=key, max_tokens=max_tokens * 2, temperature=temperature,
                             timeout=timeout, retries=retries - 1, require=require,
                             on_usage=on_usage)
    parsed = _extract_json(content)
    if require is not None and not isinstance(parsed, require):
        parsed = None
    if parsed is None:
        # 语义对齐 eco_note 实测版：垃圾内容 → []（调用方照常推进）；
        # 截断（length 且重试耗尽）→ TruncatedResponseError（调用方不得推进水位线）
        if finish == "length":
            raise TruncatedResponseError("finish_reason=length 且重试耗尽，解析为空")
        if require is dict:
            raise MalformedResponseError(f"内容无法解析为 dict：{content[:80]!r}")
        return []
    return parsed
