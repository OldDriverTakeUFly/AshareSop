# davis_analyzer/recap/scriptwriter.py
"""recap 剧本生成:LLM 双解说(NBA 风格),数字全注入、validator 自纠错循环。"""
from __future__ import annotations

import json
import re
from decimal import Decimal

from loguru import logger

from davis_analyzer.recap.constants import NBA_STYLE_TABLE
from davis_analyzer.recap.types import Candidate, DialogueLine, Episode, EpisodeSegment
from davis_analyzer.recap.validator import validate_episode

_MAX_ATTEMPTS = 2
_TEMPERATURE = 0.7


class ScriptGenError(RuntimeError):
    """剧本生成失败(LLM 不可用/解析失败/自纠错后仍不过闸)。"""


_SYSTEM = (
    "你是A股盘后复盘短视频的金牌编剧,风格对标NBA赛事转播解说。"
    "两位解说:pb=实况解说(激情,喊动作、短句、有画面感),color=嘉宾分析(娓娓道来,讲资金与板块逻辑)。"
    "铁律:①解说词中任何数字(点位/涨幅/连板数/炸板次数/振幅/金额/家数)只准使用【可用事实】清单里给出的数字,"
    "一个都不能自己编、不能换算;②只描述已发生的事实,不给任何操作建议;"
    "③末段(outlook)必须原话包含「不构成投资建议」;④输出只给一个JSON对象,不要多余文字。"
)


def _fact(fid: str, value, unit: str, display: str, day: str, ref: str) -> dict:
    s = format(Decimal(str(value)), "f")
    return {"id": fid, "value": s.rstrip("0").rstrip(".") if "." in s else s, "unit": unit,
            "display": display, "as_of": day, "source": {"kind": "stockhot", "ref": ref}}


def scoreboard_facts(bundle: dict, day: str) -> list[dict]:
    """片头比分牌 facts:三大指数 close/pct_chg + 涨跌家数 + 涨停家数。"""
    out: list[dict] = []
    for ix in bundle.get("index", []):
        key = {"000001.SH": "sh", "399001.SZ": "sz", "399006.SZ": "cyb"}[ix["code"]]
        # value 与 display 同口径取整(3875.6→3876):解说文本只能复述 display,
        # 若 value 存原始小数,数字闸会永远对不上
        out.append(_fact(f"idx_{key}_close", f"{ix['close']:.0f}", "点",
                         f"{ix['name']}{ix['close']:.0f}点", day,
                         f"market_data.db:index_daily@{day}:{ix['code']}:close"))
        out.append(_fact(f"idx_{key}_chg", f"{abs(ix['pct_chg']):.2f}", "%",
                         f"{ix['name']}{ix['pct_chg']:+.2f}%", day,
                         f"market_data.db:index_daily@{day}:{ix['code']}:pct_chg"))
    br = bundle.get("breadth", {})
    out.append(_fact("breadth_up", br.get("up", 0), "家", f"上涨{br.get('up', 0)}家", day,
                     f"market_data.db:daily_price@{day}:pct_chg>0"))
    out.append(_fact("breadth_down", br.get("down", 0), "家", f"下跌{br.get('down', 0)}家", day,
                     f"market_data.db:daily_price@{day}:pct_chg<0"))
    out.append(_fact("limit_up_count", bundle.get("limit_up_count", 0), "家",
                     f"涨停{bundle.get('limit_up_count', 0)}家", day,
                     f"stockhot.db:limit_up_pool@{day}:count"))
    return out


def build_user_prompt(cands: list[Candidate], bundle: dict) -> str:
    style = "\n".join(f"- {a} → {b}" for a, b in NBA_STYLE_TABLE)
    idx = ";".join(f"{i['name']} {i['close']:.0f}点 {i['pct_chg']:+.2f}%" for i in bundle["index"])
    lines = [
        f"## 今日战报(片头比分牌素材)\n{idx};上涨{bundle['breadth']['up']}家/下跌{bundle['breadth']['down']}家;"
        f"涨停{bundle['limit_up_count']}家。",
        "## 高光候选(按戏剧性降序)",
    ]
    for i, c in enumerate(cands, 1):
        facts = ";".join(f["display"] for f in c.facts) or "无"
        lines.append(
            f"{i}. {c.name}({c.ts_code},板块:{c.sector or '未知'}) 剧情点:{'、'.join(c.notes) or '常规'};"
            f"回放窗 {c.replay_start[:5]}-{c.replay_end[:5]};事实:{facts}"
            + (";教育性段落(讲板块逻辑)" if c.educational else ""))
    ladder = ";".join(
        f"{t['board_count']}板:" + ",".join(s.get("name", "?") for s in t["stocks"][:3])
        for t in bundle.get("boards", [])[:3])
    lines.append(f"## 连板天梯(积分榜)\n{ladder or '无'}")
    lines.append("## NBA 转播语言映射(风格参考,别逐字照搬)\n" + style)
    all_facts = scoreboard_facts(bundle, cands[0].facts[0]["as_of"] if cands and cands[0].facts else "1970-01-01") \
        + [f for c in cands for f in c.facts]
    lines.append("## 可用事实(数字唯一来源,逐条给出 value+unit)\n" +
                 "\n".join(f"- {f['display']}" for f in all_facts))
    lines.append(
        "## 输出 JSON schema(严格照此结构)\n"
        '{"title": "本期标题(15字内,有NBA味)", "segments": ['
        '{"seg_id": "open", "kind": "scoreboard", "ts_code": null, "lines": [{"speaker": "pb", "text": "..."}]},'
        '... 每只候选一个 {"seg_id": "s1", "kind": "stock", "ts_code": "605577.SH", '
        '"lines": [pb/color 交替 3-6 句,回放窗时间内讲故事]} ...,'
        '{"seg_id": "close", "kind": "outlook", "ts_code": null, '
        '"lines": [2-3句,末句含免责原话]}]}\n'
        "总时长预算:全部台词合计 180-500 字。")
    return "\n\n".join(lines)


def parse_episode_json(content: str) -> dict:
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        raise ScriptGenError(f"LLM 输出无 JSON 对象: {content[:120]!r}")
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ScriptGenError(f"LLM JSON 解析失败: {e}") from e
    if "segments" not in d or "title" not in d:
        raise ScriptGenError("LLM JSON 缺 title/segments")
    return d


def assemble_episode(trade_date: str, cands: list[Candidate], bundle: dict, data: dict) -> Episode:
    allowed_kinds = {"scoreboard", "stock", "outlook"}
    segs: list[EpisodeSegment] = []
    for s in data["segments"]:
        if s.get("kind") not in allowed_kinds:
            raise ScriptGenError(f"非法 segment kind={s.get('kind')!r}")
        segs.append(EpisodeSegment(
            seg_id=str(s.get("seg_id", "")), kind=s["kind"],
            ts_code=s.get("ts_code"),
            lines=[DialogueLine(speaker=l["speaker"], text=str(l["text"]).strip())
                   for l in s.get("lines", []) if l.get("text")]))
    facts = scoreboard_facts(bundle, trade_date) + [f for c in cands for f in c.facts]
    return Episode(trade_date=trade_date, title=str(data["title"]), segments=segs, facts=facts)


def generate_episode(trade_date: str, cands: list[Candidate], bundle: dict,
                     provider=None) -> Episode:
    if not cands:
        raise ScriptGenError("候选为空(冰点日应由调用方走降级剧本)")
    if provider is None:
        from stockhot.advisor.llm_provider import get_provider
        provider = get_provider()
    prompt = build_user_prompt(cands, bundle)
    last_fails: list[str] = []
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = provider.complete(prompt, system=_SYSTEM, max_tokens=2000,
                                     temperature=_TEMPERATURE)
            data = parse_episode_json(resp.content)
        except ScriptGenError as e:
            last_fails = [f"attempt {attempt}: {e}"]
            logger.warning(f"recap 剧本 attempt{attempt} 失败: {e}")
            continue
        ep = assemble_episode(trade_date, cands, bundle, data)
        last_fails = validate_episode(ep)
        if not last_fails:
            return ep
        logger.warning(f"recap 剧本 attempt{attempt} 未过闸: {last_fails[:3]}")
        prompt = (f"{build_user_prompt(cands, bundle)}\n\n"
                  f"## 上一稿未通过质检,必须修复以下问题后重写\n"
                  + "\n".join(f"- {f}" for f in last_fails))
    raise ScriptGenError(f"自纠错 {_MAX_ATTEMPTS} 次仍未过闸: {last_fails[:5]}")
