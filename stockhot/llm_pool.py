"""llm_pool.py — LLM API 多池轮换:配置解析+候选枚举+错误分类+冷却台账。

**不做 HTTP 调用**——消费方保留各自原生传输(OpenAI SDK/httpx),遍历
candidates() 自行调用,把 (status_code, message) 交 classify() 定类、
note() 记账;全枯竭时 summarize() 给各池死因摘要。

配置(env,编号池,管道分隔,断号即池列表结束):
    LLM_POOL_1=<base_url>|<api_key>|<model1[,model2,…]>
模型名可加 :vision / :novision 后缀显式覆盖能力表(默认按内置能力表)。
无 LLM_POOL_* 时从 LLM_BASE_URL|LLM_API_KEY|LLM_MODEL 合成 1 池,
glm 系补已知 fallback 模型——现有 env 零改动,行为与单池等价。

错误分类与处置:
    1310 额度尽 → 解析「限额将在 <ts> 重置」,该池冷却至该时刻
                  (解析失败退避 30 分钟);瞬态频率错误(1302)不冷却。
    401/403    → 池死(本进程内不再出候选)。
冷却台账为进程内 dict——各管线皆短命进程,不做跨进程持久化。
"""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timedelta
from typing import NamedTuple

# 视觉能力表(可被配置后缀 :vision/:novision 逐模型覆盖)
_VISION_CAPABLE = {"glm-5.3-flash", "glm-4.5v-flash", "glm-4v-flash",
                   "glm-4v-plus", "glm-4v"}
# 合成池时 glm 系补齐的 fallback 模型(配置模型在前,去重)
_GLM_TEXT_FALLBACKS = ["glm-5.3-flash", "glm-4-flash"]
_RESET_RE = re.compile(r"限额将在\s*(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2})")
_QUOTA_FALLBACK = timedelta(minutes=30)
_MAX_POOLS = 16  # 断号截断上限(防脏配置狂读)

_MODEL_SUFFIX_RE = re.compile(r"^(.*?):(vision|novision)$")


class Candidate(NamedTuple):
    pool: str
    base_url: str
    api_key: str
    model: str


_lock = threading.Lock()
_pool_cooldown: dict[str, datetime] = {}   # pool 名 → 冷却至(额度尽)
_dead_pools: set[str] = set()              # 鉴权失败,本进程内不再出候选
_last_error: dict[str, str] = {}           # pool 名 → 最近死因(摘要用)


def _synth_pool() -> tuple[str, str, str, list[str]] | None:
    """无 LLM_POOL_* 时从 LLM_* 三件套合成 1 池;三件套缺 key 返回 None。"""
    base = (os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("LLM_API_KEY") or "").strip()
    model = (os.environ.get("LLM_MODEL") or "").strip()
    if not key:
        return None
    models = [m for m in [model] if m]
    for fb in _GLM_TEXT_FALLBACKS:  # glm 系补已知 fallback,去重保序
        if not any(m.split(":")[0] == fb for m in models):
            models.append(fb)
    return ("1", base, key, models)


def _pools() -> list[tuple[str, str, str, list[str]]]:
    """[(pool名, base_url, api_key, [模型…])]——编号池优先,缺省合成。"""
    pools: list[tuple[str, str, str, list[str]]] = []
    for n in range(1, _MAX_POOLS + 1):
        raw = (os.environ.get(f"LLM_POOL_{n}") or "").strip()
        if not raw:
            break  # 断号即池列表结束
        parts = raw.split("|")
        if len(parts) != 3:
            continue  # 脏行跳过不炸
        base, key, models_raw = (p.strip() for p in parts)
        models = [m.strip() for m in models_raw.split(",") if m.strip()]
        if base and key and models:
            pools.append((str(n), base.rstrip("/"), key, models))
    if not pools:
        synth = _synth_pool()
        if synth:
            pools.append(synth)
    return pools


def candidates(vision: bool = False) -> list[Candidate]:
    """按池序×池内模型序展开候选;跳过冷却中/死池;vision=True 再滤无视觉模型。"""
    now = datetime.now()
    out: list[Candidate] = []
    with _lock:
        for name, base, key, models in _pools():
            if name in _dead_pools:
                continue
            cd = _pool_cooldown.get(name)
            if cd is not None and cd > now:
                continue
            for raw in models:
                m = _MODEL_SUFFIX_RE.match(raw)
                override = None if m is None else (m.group(2) == "vision")
                model = raw.split(":")[0]
                capable = override if override is not None else (model in _VISION_CAPABLE)
                if vision and not capable:
                    continue
                out.append(Candidate(name, base, key, model))
    return out


def classify(status_code: int | None, message: str) -> str:
    """(HTTP 状态码, 报文文本) → verdict;识别不了归 network(fail-closed 换候选)。"""
    msg = message or ""
    if status_code == 429:
        return "quota" if "1310" in msg else ("rate" if "1302" in msg else "rate")
    if status_code == 400 and "model" in msg.lower():
        return "model"
    if status_code in (401, 403):
        return "auth"
    if status_code == 200:
        return "ok"
    return "network"


def note(cand: Candidate, verdict: str, message: str = "") -> None:
    """按 verdict 记账:quota→冷却(解析重置时间,失败退避 30 分钟);auth→池死;
    rate/model/network/ok→只留最近错误供摘要。"""
    with _lock:
        _last_error[cand.pool] = (message or verdict)[:200]
        if verdict == "quota":
            m = _RESET_RE.search(message or "")
            until = (datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") if m
                     else datetime.now() + _QUOTA_FALLBACK)
            cur = _pool_cooldown.get(cand.pool)
            if cur is None or until > cur:
                _pool_cooldown[cand.pool] = until
        elif verdict == "auth":
            _dead_pools.add(cand.pool)


def summarize() -> str:
    """全枯竭时的人话摘要:各池状态+最近死因。"""
    now = datetime.now()
    parts = []
    with _lock:
        for name, _base, _key, _models in _pools():
            if name in _dead_pools:
                parts.append(f"池{name}: 鉴权失败({_last_error.get(name, '')})")
            elif name in _pool_cooldown and _pool_cooldown[name] > now:
                parts.append(f"池{name}: 额度冷却至 {_pool_cooldown[name]:%m-%d %H:%M:%S}"
                             f"({_last_error.get(name, '')})")
            else:
                parts.append(f"池{name}: {_last_error.get(name, '无候选')}")
    return "; ".join(parts) or "无可用池"
