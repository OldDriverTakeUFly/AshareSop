# davis_analyzer/recap/selector.py
"""recap 选片引擎:当日戏剧性评分(节目效果分,非投资分)。"""
from __future__ import annotations

from decimal import Decimal

from davis_analyzer.core.constants import RECAP_DRAMA_WEIGHTS as W
from davis_analyzer.recap.types import Candidate, DramaEvent

_LATE_SEAL = "14:30:00"
_EDU_SECTOR_LIMITUPS = 3     # 板块涨停家数门槛=可讲板块叙事(教育性段落)
_AMP_FOR_LONG_LEG = 15.0


def _fact(fid: str, value, unit: str, display: str, day: str, ref: str) -> dict:
    """Fact.to_dict 兼容形态(source=stockhot 指纹,当日过期)。"""
    s = format(Decimal(str(value)), "f")
    return {"id": fid, "value": s.rstrip("0").rstrip(".") if "." in s else s,
            "unit": unit, "display": display, "as_of": day,
            "source": {"kind": "stockhot", "ref": ref}}


def _window(first: str, last: str) -> tuple[str, str]:
    """回放窗:首封前 20 分钟 ~ 最后封板后 5 分钟,夹在 09:30-15:00 内。"""
    def shift(hhmmss: str, minutes: int) -> str:
        h, m, _ = (int(x) for x in hhmmss.split(":"))
        total = max(9 * 60 + 30, min(15 * 60, h * 60 + m + minutes))
        return f"{total // 60:02d}:{total % 60:02d}:00"
    start = shift(first, -20) if first else "09:30:00"
    end = shift(last, 5) if last else "15:00:00"
    return start, end


def score_day(bundle: dict, day: str = "1970-01-01") -> list[Candidate]:
    cands: dict[str, Candidate] = {}

    def get(code: str, name: str, sector: str) -> Candidate:
        if code not in cands:
            cands[code] = Candidate(ts_code=code, name=name, sector=sector, drama_score=0.0)
        return cands[code]

    max_board = max((int(t["board_count"]) for t in bundle.get("boards", [])), default=0)
    sector_counts: dict[str, int] = {}
    for r in bundle.get("pool", []):
        sector_counts[r.get("sector", "")] = sector_counts.get(r.get("sector", ""), 0) + 1
    amp_map = {a["ts_code"]: a["amplitude_pct"] for a in bundle.get("amplitude_top", [])}
    down_roots = {r["ts_code"].split(".")[0] for r in bundle.get("down", [])}
    top_broker_net = max((float(b.get("net_amount") or 0) for b in bundle.get("brokers", [])),
                         default=0.0)
    lhb_map = {str(r.get("code")): r for r in bundle.get("lhb_detail", [])}

    for r in bundle.get("pool", []):
        code, name, sector = r["ts_code"], r.get("name", ""), r.get("sector", "")
        c, notes = get(code, name, sector), []

        ref = f"stockhot.db:limit_up_pool@{day}:{code}"

        def add(kind: str, label: str, score: float, detail: dict) -> None:
            c.events.append(DramaEvent(kind, label, score, detail))
            c.drama_score += score
            notes.append(label)

        add("limit_up", f"涨停收盘({r.get('change_pct', 0):+.2f}%)",
            W["limit_up_base"], {"change_pct": r.get("change_pct")})
        c.facts.append(_fact(f"{code}_chg", abs(r.get("change_pct", 0)), "%",
                             f"{r.get('change_pct', 0):+.2f}%", day, ref))
        broken = int(r.get("broken_count") or 0)
        if broken:
            add("reseal", f"{broken} 度炸板后回封", W["reseal_per_broken"] * broken,
                {"broken_count": broken})
            c.facts.append(_fact(f"{code}_broken", broken, "次", f"{broken}次炸板", day, ref))
        last_seal = r.get("last_seal_time", "")
        if last_seal >= _LATE_SEAL:
            add("reseal_late", f"尾盘回封({last_seal[:5]})", W["reseal_late"],
                {"last_seal_time": last_seal})
            c.facts.append(_fact(f"{code}_lastseal", last_seal[:5].replace(":", ""),
                                 "", f"{last_seal[:5]}回封", day, ref))
        boards = int(r.get("consecutive_boards") or 1)
        c.facts.append(_fact(f"{code}_boards", boards, "板", f"{boards}连板", day, ref))
        if boards == max_board and max_board >= 2:
            add("ladder", f"积分榜最高板({boards}板)",
                W["ladder_top"] + W["ladder_extra_per_board"] * (boards - 1),
                {"boards": boards})
        elif boards >= 3:
            add("ladder", f"{boards}连板", W["streak_3plus"], {"boards": boards})
        if code.split(".")[0] in down_roots:
            add("earth_sky", "地天板级大逆转", W["earth_sky"], {})
        amp = amp_map.get(code)
        if amp is not None and amp >= _AMP_FOR_LONG_LEG:
            add("long_leg", f"大长腿(振幅{amp:.1f}%)", W["long_leg_amp"], {"amplitude_pct": amp})
            c.facts.append(_fact(f"{code}_amp", amp, "%", f"振幅{amp:.1f}%", day, ref))
        if code in bundle.get("lhb_codes", set()):
            add("lhb", "龙虎榜球星对位", W["lhb_listed"], {})
            net = float(lhb_map.get(code, {}).get("net_buy_amount") or 0)
            if abs(net) > 1e8:
                add("lhb", "亿元级席位净买(巨星对决)", W["lhb_big_broker"], {})
                c.facts.append(_fact(f"{code}_lhbnb", round(net / 1e8, 2), "亿",
                                     f"龙虎榜净买{net / 1e8:+.2f}亿", day, ref))
        c.replay_start, c.replay_end = _window(r.get("first_seal_time", ""), last_seal)
        c.notes = notes
        c.educational = sector_counts.get(sector, 0) >= _EDU_SECTOR_LIMITUPS

    for r in bundle.get("broken", []):
        code, name, sector = r["ts_code"], r.get("name", ""), r.get("sector", "")
        c = get(code, name, sector)
        broken = int(r.get("broken_count") or 1)
        c.events.append(DramaEvent("broken", f"收盘炸板({broken}次炸开)",
                                   W["broken_close_base"] + W["reseal_per_broken"] * (broken - 1),
                                   {"broken_count": broken}))
        c.drama_score += W["broken_close_base"] + W["reseal_per_broken"] * (broken - 1)
        c.notes.append(f"收盘炸板(被帽戏码)")
        c.facts.append(_fact(f"{code}_brkclose", broken, "次", f"收盘仍炸板({broken}次)",
                             day, f"stockhot.db:broken_pool@{day}:{code}"))

    for a in bundle.get("amplitude_top", []):
        code = a["ts_code"]
        if code in cands:          # 已入池的由 pool 路径记 long_leg
            continue
        name = bundle.get("names", {}).get(code, "")
        c = get(code, name, "")
        c.events.append(DramaEvent("long_leg", f"大长腿(振幅{a['amplitude_pct']:.1f}%)",
                                   W["long_leg_amp"], {"amplitude_pct": a["amplitude_pct"]}))
        c.drama_score += W["long_leg_amp"]
        c.facts.append(_fact(f"{code}_amp", a["amplitude_pct"], "%",
                             f"振幅{a['amplitude_pct']:.1f}%", day,
                             f"market_data.db:daily_price@{day}:{code}"))

    return sorted(cands.values(), key=lambda c: -c.drama_score)


def select_candidates(bundle: dict, day: str = "1970-01-01",
                      max_count: int = 5, per_sector_cap: int = 2) -> list[Candidate]:
    cands = score_day(bundle, day)
    if not cands:
        return []
    picked: list[Candidate] = []
    sector_n: dict[str, int] = {}
    for c in cands:
        if len(picked) >= max_count:
            break
        if sector_n.get(c.sector, 0) >= per_sector_cap:
            continue
        picked.append(c)
        sector_n[c.sector] = sector_n.get(c.sector, 0) + 1
    if not any(c.educational for c in picked):     # 教育性保底:换入最高分教育候选
        edu = next((c for c in cands if c.educational and c not in picked), None)
        if edu and picked:
            picked[-1] = edu
    return picked
